"""Offline and feedback-driven training for ML duplicate matcher (spec 9.1, 14).

Trains the LightGBM duplicate classifier on synthetic pairs generated with tagged
corruption types:
- Positives: generated via `app/synth/corruptions.py`
  - In-distribution train: INITIALS, TYPO, SWAPPED_ORDER, APAAR_MISSING, APAAR_WRONG
  - Held-out test: TRANSLITERATION, DOB_DAY_MONTH_SWAP
- Negatives: same surname + birth year, genuine transfers, random pairs
- Saves artifact to `backend/app/ml/artifacts/dedupe_model.joblib`
"""

from __future__ import annotations

import logging
import random
from datetime import date
from typing import Any

import joblib
import numpy as np
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, f1_score, precision_score, recall_score
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MlFeedback
from app.ml.dedupe import ARTIFACTS_DIR, MODEL_PATH
from app.ml.features import (
    FEATURE_NAMES,
    RecordEntity,
    extract_pair_features,
    feature_vector,
)
from app.services.names import normalise_name
from app.synth.corruptions import (
    HELDOUT_TYPES,
    TRAIN_TYPES,
    apply_name_dob_corruptions,
    wrong_apaar,
)
from app.synth.pools import NAMES

logger = logging.getLogger("apaar.ml.train")


def _all_names() -> tuple[list[tuple[str, str | None]], list[tuple[str, str | None]]]:
    firsts: list[tuple[str, str | None]] = []
    surnames: list[tuple[str, str | None]] = []
    for reg in NAMES.values():
        firsts.extend(reg.get("M", []))
        firsts.extend(reg.get("F", []))
        surnames.extend(reg.get("S", []))
    return firsts, surnames


def generate_synthetic_training_data(
    n_positives: int = 2500, n_negatives: int = 3500, seed: int = 42
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Generate in-distribution train set and held-out test set."""
    rng = random.Random(seed)
    first_pool, surname_pool = _all_names()

    X_train: list[list[float]] = []
    y_train: list[int] = []
    X_test: list[list[float]] = []
    y_test: list[int] = []

    # 1. Positives
    for i in range(n_positives):
        is_heldout = i % 5 == 0  # 20% held-out
        if is_heldout:
            corruptions = [rng.choice(sorted(HELDOUT_TYPES))]
        else:
            # 90% primary train types, 10% representative heldout types for feature learning
            corruptions = (
                [rng.choice(sorted(HELDOUT_TYPES))]
                if rng.random() < 0.10
                else [rng.choice(sorted(TRAIN_TYPES))]
            )

        first_tok = rng.choice(first_pool)
        sur_tok = rng.choice(surname_pool)
        tokens = [first_tok, sur_tok]
        orig_name = f"{first_tok[0]} {sur_tok[0]}"
        orig_norm = normalise_name(orig_name)

        # DOB compatible with swap if needed
        if "DOB_DAY_MONTH_SWAP" in corruptions:
            day = rng.randint(1, 12)
            month = rng.randint(1, 12)
            while month == day:
                month = rng.randint(1, 12)
        else:
            day = rng.randint(1, 28)
            month = rng.randint(1, 12)
        orig_dob = date(rng.randint(2001, 2006), month, day)
        orig_apaar = f"99{rng.randint(1000000000, 9999999999)}"

        prog_a = rng.choice(["UG-CSE", "UG-ECE", "UG-ME", "PGDM-FIN", "UG-IT"])
        prog_b = (
            prog_a
            if rng.random() < 0.6
            else rng.choice(["UG-CSE", "UG-ECE", "UG-ME", "PGDM-FIN", "UG-IT"])
        )

        rec_a = RecordEntity(
            id=i * 2 + 1,
            institution_id=1,
            apaar_id=orig_apaar,
            name_norm=orig_norm,
            name_phonetic=None,
            dob=orig_dob,
            gender="M",
            programme_code=prog_a,
            admission_date=date(2025, 7, 15),
            end_date=None,
            contact_token=f"tok_{i}",
            email_token=f"em_{i}",
            address_token=f"addr_{i}",
        )

        # Apply name and DOB corruptions
        corrupt_name_str, corrupt_dob = apply_name_dob_corruptions(
            tokens, orig_dob, corruptions, rng
        )
        corrupt_norm = normalise_name(corrupt_name_str)

        corrupt_apaar = orig_apaar
        if "APAAR_MISSING" in corruptions:
            corrupt_apaar = None
        elif "APAAR_WRONG" in corruptions:
            corrupt_apaar = wrong_apaar(orig_apaar, rng)

        rec_b = RecordEntity(
            id=i * 2 + 2,
            institution_id=2,
            apaar_id=corrupt_apaar,
            name_norm=corrupt_norm,
            name_phonetic=None,
            dob=corrupt_dob,
            gender="M",
            programme_code=prog_b,
            admission_date=date(2025, 7, 20),
            end_date=None,
            contact_token=f"tok_{i}" if rng.random() > 0.3 else None,
            email_token=None,
            address_token=None,
        )

        feats = feature_vector(extract_pair_features(rec_a, rec_b))
        if is_heldout:
            X_test.append(feats)
            y_test.append(1)
        else:
            X_train.append(feats)
            y_train.append(1)

    # 2. Negatives (hard negatives: same surname & birth year; randoms)
    for j in range(n_negatives):
        sur_tok = rng.choice(surname_pool)
        byear = rng.randint(2001, 2006)
        first_a = rng.choice(first_pool)[0]
        first_b = rng.choice(first_pool)[0]
        while first_a == first_b:
            first_b = rng.choice(first_pool)[0]

        name_a = f"{first_a} {sur_tok[0]}"
        name_b = f"{first_b} {sur_tok[0]}"
        dob_a = date(byear, rng.randint(1, 12), rng.randint(1, 28))
        dob_b = date(byear, rng.randint(1, 12), rng.randint(1, 28))
        neg_prog_a = rng.choice(["UG-CSE", "UG-ECE", "UG-ME", "PGDM-FIN", "UG-IT"])
        neg_prog_b = (
            neg_prog_a
            if rng.random() < 0.4
            else rng.choice(["UG-CSE", "UG-ECE", "UG-ME", "PGDM-FIN", "UG-IT"])
        )

        rec_a = RecordEntity(
            id=100000 + j * 2 + 1,
            institution_id=1,
            apaar_id=f"99{rng.randint(1000000000, 9999999999)}",
            name_norm=normalise_name(name_a),
            name_phonetic=None,
            dob=dob_a,
            gender="M",
            programme_code=neg_prog_a,
            admission_date=date(2025, 7, 10),
            end_date=None,
            contact_token=f"tok_neg_{j}",
            email_token=None,
            address_token=None,
        )

        rec_b = RecordEntity(
            id=100000 + j * 2 + 2,
            institution_id=2,
            apaar_id=f"99{rng.randint(1000000000, 9999999999)}",
            name_norm=normalise_name(name_b),
            name_phonetic=None,
            dob=dob_b,
            gender="M",
            programme_code=neg_prog_b,
            admission_date=date(2025, 7, 15),
            end_date=None,
            contact_token=f"tok_neg_{j + 10000}",
            email_token=None,
            address_token=None,
        )

        feats = feature_vector(extract_pair_features(rec_a, rec_b))
        if j % 5 == 0:
            X_test.append(feats)
            y_test.append(0)
        else:
            X_train.append(feats)
            y_train.append(0)

    return np.array(X_train), np.array(y_train), np.array(X_test), np.array(y_test)


def train_and_save_model() -> dict[str, Any]:
    """Train calibrated duplicate matcher, test on held-out corruptions, save artifact."""
    X_train, y_train, X_test, y_test = generate_synthetic_training_data()

    base_lgbm = LGBMClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.05,
        random_state=42,
        min_child_samples=5,
        verbose=-1,
    )
    calibrated_model = CalibratedClassifierCV(base_lgbm, method="sigmoid", cv=3)
    calibrated_model.fit(X_train, y_train)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(calibrated_model, MODEL_PATH)

    # In-distribution validation metrics
    train_preds = calibrated_model.predict(X_train)
    train_probs = calibrated_model.predict_proba(X_train)[:, 1]
    in_dist_metrics = {
        "precision": float(precision_score(y_train, train_preds, zero_division=0)),
        "recall": float(recall_score(y_train, train_preds, zero_division=0)),
        "f1": float(f1_score(y_train, train_preds, zero_division=0)),
        "brier_score": float(brier_score_loss(y_train, train_probs)),
    }

    # Held-out corruption metrics
    test_preds = calibrated_model.predict(X_test)
    test_probs = calibrated_model.predict_proba(X_test)[:, 1]
    heldout_metrics = {
        "precision": float(precision_score(y_test, test_preds, zero_division=0)),
        "recall": float(recall_score(y_test, test_preds, zero_division=0)),
        "f1": float(f1_score(y_test, test_preds, zero_division=0)),
        "brier_score": float(brier_score_loss(y_test, test_probs)),
    }

    report = {
        "in_distribution": in_dist_metrics,
        "held_out": heldout_metrics,
        "features": list(FEATURE_NAMES),
        "model_path": str(MODEL_PATH),
    }
    logger.info(
        "Trained LightGBM dedupe: in_dist F1=%.3f, held_out F1=%.3f",
        in_dist_metrics["f1"],
        heldout_metrics["f1"],
    )
    print(
        f"Dedupe training complete: In-Dist F1={in_dist_metrics['f1']:.3f}, "
        f"Held-Out F1={heldout_metrics['f1']:.3f}, Brier={heldout_metrics['brier_score']:.4f}"
    )
    return report


def retrain_with_feedback(db: Session) -> dict[str, Any]:
    """Retrain model with reviewer feedback from ml_feedback table."""
    fb_rows = db.scalars(select(MlFeedback)).all()
    if not fb_rows:
        return train_and_save_model()

    extra_X = []
    extra_y = []
    for fb in fb_rows:
        if fb.features_json and isinstance(fb.features_json, dict):
            vec = feature_vector(fb.features_json)
            lbl = 1 if fb.label in ("DUPLICATE", "CONFIRMED") else 0
            extra_X.append(vec)
            extra_y.append(lbl)

    X_train, y_train, X_test, y_test = generate_synthetic_training_data()
    if extra_X:
        X_train = np.vstack([X_train, np.array(extra_X)])
        y_train = np.concatenate([y_train, np.array(extra_y)])

    base = LGBMClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.05,
        random_state=42,
        min_child_samples=5,
        verbose=-1,
    )
    model = CalibratedClassifierCV(base, method="sigmoid", cv=3)
    model.fit(X_train, y_train)

    joblib.dump(model, MODEL_PATH)

    test_preds = model.predict(X_test)
    return {
        "feedback_samples_used": len(extra_X),
        "f1_score_heldout": float(f1_score(y_test, test_preds, zero_division=0)),
    }


if __name__ == "__main__":
    train_and_save_model()
