"""Institution anomaly detector (spec 9.2, FR-15).

Computes peer-normalised institution features for an academic year:
- fill_ratio: enrolment / approved intake
- late_admission_share: share of admissions in the last 7 days of the admission window
- category_mix_distance: Jensen-Shannon distance from peer category distribution
- supernumerary_ratio: supernumerary admissions / total
- lateral_share: lateral entry / total
- warning_share: share of records with rule warnings
- yoy_enrolment_change: year-on-year enrolment change
- apaar_mismatch_share: share of records with APAAR mismatch
- coverage_ratio: records received / declared_enrolment

IsolationForest model (contamination=0.05, random_state=42) normalises score to 0..1.
Feature explanations use per-feature peer z-scores.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.spatial.distance import jensenshannon
from sklearn.ensemble import IsolationForest

logger = logging.getLogger("apaar.ml.anomaly")

ANOMALY_FEATURE_NAMES = (
    "fill_ratio",
    "late_admission_share",
    "category_mix_distance",
    "supernumerary_ratio",
    "lateral_share",
    "warning_share",
    "yoy_enrolment_change",
    "apaar_mismatch_share",
    "coverage_ratio",
)


@dataclass
class InstitutionMetrics:
    institution_id: int
    institution_code: str
    state: str
    academic_year: str
    approved_intake: int
    declared_enrolment: int | None
    total_records: int
    active_records: int
    late_admissions: int
    supernumerary_count: int
    lateral_count: int
    warning_count: int
    apaar_mismatch_count: int
    prior_year_records: int | None
    category_counts: dict[str, int]


@dataclass
class AnomalyScore:
    institution_id: int
    institution_code: str
    academic_year: str
    anomaly_score: float  # 0.0 to 1.0 (higher = more anomalous)
    is_anomalous: bool
    rank: int
    top_features: list[dict[str, Any]]
    raw_features: dict[str, float]


def compute_raw_features(
    inst: InstitutionMetrics, peer_cat_dist: np.ndarray | None = None
) -> dict[str, float]:
    """Compute raw un-normalised feature dictionary for an institution."""
    tot = max(inst.total_records, 1)

    fill = min(inst.total_records / max(inst.approved_intake, 1), 2.5)
    late = inst.late_admissions / tot
    supernum = inst.supernumerary_count / tot
    lateral = inst.lateral_count / tot
    warning = inst.warning_count / tot
    mismatch = inst.apaar_mismatch_count / tot

    coverage = 1.0
    if inst.declared_enrolment and inst.declared_enrolment > 0:
        coverage = min(inst.total_records / inst.declared_enrolment, 2.0)

    yoy = 0.0
    if inst.prior_year_records is not None and inst.prior_year_records > 0:
        yoy = (inst.total_records - inst.prior_year_records) / inst.prior_year_records

    # Jensen-Shannon category distribution distance
    cat_keys = ("GENERAL", "SC", "ST", "OBC", "EWS", "OTHER")
    inst_vec = np.array([inst.category_counts.get(k, 0) for k in cat_keys], dtype=float)
    if inst_vec.sum() > 0:
        inst_vec /= inst_vec.sum()
    else:
        inst_vec = np.ones(len(cat_keys)) / len(cat_keys)

    if peer_cat_dist is None or peer_cat_dist.sum() == 0:
        cat_dist = 0.0
    else:
        cat_dist = float(jensenshannon(inst_vec, peer_cat_dist))

    return {
        "fill_ratio": round(fill, 4),
        "late_admission_share": round(late, 4),
        "category_mix_distance": round(cat_dist, 4),
        "supernumerary_ratio": round(supernum, 4),
        "lateral_share": round(lateral, 4),
        "warning_share": round(warning, 4),
        "yoy_enrolment_change": round(yoy, 4),
        "apaar_mismatch_share": round(mismatch, 4),
        "coverage_ratio": round(coverage, 4),
    }


class AnomalyDetector:
    """Isolation Forest anomaly detector with peer z-score explanations."""

    def __init__(self, contamination: float = 0.05, seed: int = 42) -> None:
        self.contamination = contamination
        self.seed = seed
        self.model = IsolationForest(
            contamination=self.contamination, random_state=self.seed, n_estimators=100
        )

    def analyze_institutions(self, institutions: list[InstitutionMetrics]) -> list[AnomalyScore]:
        """Score institutions and generate z-score explanations."""
        if not institutions:
            return []

        # 1. Compute aggregate peer category distribution
        cat_keys = ("GENERAL", "SC", "ST", "OBC", "EWS", "OTHER")
        tot_cats = np.zeros(len(cat_keys), dtype=float)
        for inst in institutions:
            for i, k in enumerate(cat_keys):
                tot_cats[i] += inst.category_counts.get(k, 0)
        peer_cat_dist = tot_cats / max(tot_cats.sum(), 1.0)

        # 2. Compute raw features for all institutions
        raw_list = [compute_raw_features(inst, peer_cat_dist) for inst in institutions]
        X = np.array([[raw[f] for f in ANOMALY_FEATURE_NAMES] for raw in raw_list])

        # 3. Peer normalization stats (mean & std per feature)
        means = np.mean(X, axis=0)
        stds = np.std(X, axis=0)
        stds[stds == 0] = 1.0  # prevent div by zero
        Z = (X - means) / stds

        # 4. Fit Isolation Forest
        if len(institutions) >= 5:
            self.model.fit(Z)
            # decision_function gives negative for anomalies, positive for inliers
            raw_scores = -self.model.decision_function(Z)
            # min-max normalize raw_scores to [0, 1]
            s_min, s_max = raw_scores.min(), raw_scores.max()
            if s_max > s_min:
                norm_scores = (raw_scores - s_min) / (s_max - s_min)
            else:
                norm_scores = np.full(len(institutions), 0.5)
        else:
            # For small batches, use mean absolute z-score
            norm_scores = np.clip(np.abs(Z).mean(axis=1) / 3.0, 0.0, 1.0)

        # 5. Build results with top contributing feature z-scores
        results: list[AnomalyScore] = []
        for i, inst in enumerate(institutions):
            score = float(norm_scores[i])
            z_row = Z[i]
            # Rank features by absolute deviation
            feat_devs = []
            for j, fname in enumerate(ANOMALY_FEATURE_NAMES):
                feat_devs.append(
                    {
                        "feature": fname,
                        "z_score": round(float(z_row[j]), 2),
                        "value": raw_list[i][fname],
                        "peer_mean": round(float(means[j]), 3),
                    }
                )
            feat_devs.sort(key=lambda d: abs(d["z_score"]), reverse=True)

            is_anom = bool(score >= 0.70 or abs(z_row).max() >= 3.0)
            results.append(
                AnomalyScore(
                    institution_id=inst.institution_id,
                    institution_code=inst.institution_code,
                    academic_year=inst.academic_year,
                    anomaly_score=round(score, 4),
                    is_anomalous=is_anom,
                    rank=0,  # set below
                    top_features=feat_devs[:4],
                    raw_features=raw_list[i],
                )
            )

        # Sort by anomaly score descending to set ranks
        results.sort(key=lambda a: a.anomaly_score, reverse=True)
        for r, res in enumerate(results, start=1):
            res.rank = r

        return results


default_anomaly_detector = AnomalyDetector()
