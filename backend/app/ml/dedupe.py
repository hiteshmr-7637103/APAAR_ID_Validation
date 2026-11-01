"""Fuzzy duplicate matcher with blocking, LightGBM, and calibration (spec 9.1).

Stage 1: Blocking reduces O(N^2) all-pairs comparison to candidate pairs sharing keys:
- Phonetic key of surname + birth year
- First 3 chars of normalised name + DOB
- Identical contact_token (phone)
- Lexical similarity > 0.65 on name_norm

Stage 2: Pairwise classification via LightGBM + Platt calibration.
Deterministic transfer check applied before routing: genuine transfers produce no case.
Routing thresholds (FR-14):
- >= 0.90: high-priority case (priority 1, "likely duplicate")
- 0.60 - 0.90: review case (priority 2, "possible duplicate")
- < 0.60: ignore
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from lightgbm import LGBMClassifier
from metaphone import doublemetaphone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, f1_score, precision_score, recall_score

from app.ml.features import (
    RecordEntity,
    extract_pair_features,
    feature_vector,
)

logger = logging.getLogger("apaar.ml.dedupe")
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "dedupe_model.joblib"


def get_blocking_keys(record: RecordEntity) -> list[str]:
    """Generate candidate blocking keys for a record."""
    keys: list[str] = []
    # 1. Phonetic key of surname + birth year
    if record.dob:
        tokens = record.name_norm.split()
        surname = tokens[-1] if tokens else record.name_norm
        dm = doublemetaphone(surname)[0] or surname[:4]
        keys.append(f"ph_dob:{dm}:{record.dob.year}")

    # 2. First 3 chars of normalised name + DOB
    if record.dob and len(record.name_norm) >= 3:
        keys.append(f"n3_dob:{record.name_norm[:3]}:{record.dob.isoformat()}")

    # 3. Same contact_token (phone HMAC)
    if record.contact_token:
        keys.append(f"ct:{record.contact_token}")

    # 4. Same email_token
    if record.email_token:
        keys.append(f"em:{record.email_token}")

    return keys


@dataclass
class DuplicateMatchResult:
    record_a_id: int
    record_b_id: int
    probability: float
    is_transfer: bool
    evidence: dict[str, Any]
    priority: int
    severity: str


class DuplicateMatcher:
    """Calibrated LightGBM model for pairwise duplicate classification."""

    def __init__(self) -> None:
        self.model: Any = None
        self._load_or_init()

    def _load_or_init(self) -> None:
        if MODEL_PATH.exists():
            try:
                self.model = joblib.load(MODEL_PATH)
                logger.info("Loaded pre-trained dedupe model from %s", MODEL_PATH)
                return
            except Exception as e:
                logger.warning("Could not load dedupe model (%s); will train on first use", e)
        self.model = self._create_default_model()

    def _create_default_model(self) -> CalibratedClassifierCV:
        """Create baseline calibrated model."""
        base = LGBMClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.05,
            random_state=42,
            min_child_samples=5,
            verbose=-1,
        )
        return CalibratedClassifierCV(base, method="sigmoid", cv=3)

    def is_fitted(self) -> bool:
        return self.model is not None and hasattr(self.model, "calibrated_classifiers_")

    def fit(self, X: np.ndarray, y: np.ndarray) -> dict[str, float]:
        """Fit model and return training evaluation metrics."""
        self.model = self._create_default_model()
        self.model.fit(X, y)

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, MODEL_PATH)

        preds = self.model.predict(X)
        probs = self.model.predict_proba(X)[:, 1]
        metrics = {
            "precision": float(precision_score(y, preds, zero_division=0)),
            "recall": float(recall_score(y, preds, zero_division=0)),
            "f1": float(f1_score(y, preds, zero_division=0)),
            "brier_score": float(brier_score_loss(y, probs)),
        }
        logger.info(
            "Trained duplicate matcher: F1=%.3f, Brier=%.4f",
            metrics["f1"],
            metrics["brier_score"],
        )
        return metrics

    def predict_pair(self, a: RecordEntity, b: RecordEntity) -> float:
        """Predict calibrated match probability between pair (A, B)."""
        feats = extract_pair_features(a, b)
        vec = np.array([feature_vector(feats)])

        # Strong signal: identical APAAR with overlapping intervals is definite duplicate
        if feats.get("apaar_equal") == 1.0 and feats.get("interval_overlap") == 1.0:
            return 0.98

        if self.is_fitted():
            prob = float(self.model.predict_proba(vec)[0, 1])
        else:
            # High-confidence heuristic fallback if model not yet fitted
            score = (
                0.40 * feats["jaro_winkler"]
                + 0.20 * feats["token_sort_ratio"]
                + 0.20 * feats["dob_exact"]
                + 0.15 * feats["phone_match"]
                + 0.05 * feats["phonetic_match"]
            )
            if feats["dob_day_month_swap"] and feats["token_sort_ratio"] > 0.85:
                score = max(score, 0.91)
            if feats["initials_match"] > 0.85 and feats["dob_exact"]:
                score = max(score, 0.88)
            prob = float(np.clip(score, 0.0, 1.0))

        return prob

    def match_against_stored(
        self,
        new_records: list[RecordEntity],
        stored_records: list[RecordEntity],
        existing_pairs: set[tuple[int, int]] | None = None,
    ) -> list[DuplicateMatchResult]:
        """Block and evaluate new records against existing stored records.

        Avoids comparing records from the same institution (since FR-8 exact same-inst
        duplicate is handled by deterministic rules).
        """
        seen_pairs = set(existing_pairs or set())
        results: list[DuplicateMatchResult] = []

        # 1. Build inverted blocking index on stored records
        index: dict[str, list[RecordEntity]] = defaultdict(list)
        for rec in stored_records:
            for key in get_blocking_keys(rec):
                index[key].append(rec)

        # 2. Candidate generation via blocking keys
        candidates_by_new: dict[int, dict[int, RecordEntity]] = defaultdict(dict)
        for n_rec in new_records:
            for key in get_blocking_keys(n_rec):
                for candidate in index.get(key, []):
                    # Only compare across distinct institutions
                    if candidate.institution_id != n_rec.institution_id:
                        candidates_by_new[n_rec.id][candidate.id] = candidate

        # 3. Score candidate pairs
        new_by_id = {r.id: r for r in new_records}
        for new_id, candidates in candidates_by_new.items():
            a = new_by_id[new_id]
            for b in candidates.values():
                pair_key = (min(a.id, b.id), max(a.id, b.id))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                # Transfer-aware rule (spec 9.1):
                # If other enrolment ended on/before new admission date, treat as transfer (no case)
                is_transfer = False
                if b.end_date and a.admission_date and b.end_date <= a.admission_date:
                    is_transfer = True
                elif a.end_date and b.admission_date and a.end_date <= b.admission_date:
                    is_transfer = True

                feats = extract_pair_features(a, b)
                prob = self.predict_pair(a, b)

                if is_transfer:
                    # Transfer detected -> omit from duplicate cases per Spec 9.1
                    continue

                if prob >= 0.60:
                    priority = 1 if prob >= 0.90 else 2
                    severity = "WARNING"
                    evidence = {
                        "match_probability": round(prob, 4),
                        "features": feats,
                        "label": "likely_duplicate" if prob >= 0.90 else "possible_duplicate",
                        "record_a": {
                            "id": a.id,
                            "institution_id": a.institution_id,
                            "name_norm": a.name_norm,
                            "dob": a.dob.isoformat() if a.dob else None,
                            "programme_code": a.programme_code,
                        },
                        "record_b": {
                            "id": b.id,
                            "institution_id": b.institution_id,
                            "name_norm": b.name_norm,
                            "dob": b.dob.isoformat() if b.dob else None,
                            "programme_code": b.programme_code,
                        },
                    }
                    results.append(
                        DuplicateMatchResult(
                            record_a_id=a.id,
                            record_b_id=b.id,
                            probability=prob,
                            is_transfer=is_transfer,
                            evidence=evidence,
                            priority=priority,
                            severity=severity,
                        )
                    )

        return results


default_matcher = DuplicateMatcher()
