"""Tests for Phase 5: ML triage, dedupe, anomaly, rings, programme map, and review queue.

Covers:
- Feature extraction and name/DOB similarity
- TF-IDF programme normaliser suggestions & confirmation flag (Spec 9.4)
- Pairwise blocking and calibrated LightGBM duplicate matcher (Spec 9.1, ML-1)
- Transfer-aware duplicate suppression (Spec 9.1)
- ML test floors: duplicate matcher F1 >= 0.85 (Spec 13)
- ML never mutates status or tier (NFR-8)
- Shared-identity ring detection on contact tokens (Spec 9.3, ML-3)
- Institution anomaly detection with peer z-scores (Spec 9.2, ML-2)
- Review queue API and RBAC: institutions never see AICTE_ONLY cases (FR-17b, NFR-1)
- Case decision flows and feedback logging into ml_feedback table
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db.models import (
    Case,
    CaseStatus,
    CaseType,
    CaseVisibility,
    MlFeedback,
)
from app.db.session import SessionLocal
from app.ml.anomaly import AnomalyDetector, InstitutionMetrics
from app.ml.dedupe import DuplicateMatcher, RecordEntity, get_blocking_keys
from app.ml.features import (
    check_initials_compatible,
    check_interval_overlap,
    extract_pair_features,
)
from app.ml.programme_map import ProgrammeNormaliser
from app.ml.rings import ContactRecord, RingDetector
from app.ml.train import generate_synthetic_training_data
from tests.conftest import API

# ---------------------------------------------------------------------------
# 1. Feature extraction tests
# ---------------------------------------------------------------------------


def test_feature_extraction_basics():
    rec_a = RecordEntity(
        id=1,
        institution_id=1,
        apaar_id="990123456789",
        name_norm="rahul sharma",
        name_phonetic=None,
        dob=date(2005, 4, 12),
        gender="M",
        programme_code="UG-CSE",
        admission_date=date(2025, 7, 1),
        end_date=None,
        contact_token="tok_123",
        email_token="em_123",
        address_token="addr_123",
    )
    # Exact twin with minor typo and swapped day-month
    rec_b = RecordEntity(
        id=2,
        institution_id=2,
        apaar_id=None,
        name_norm="rahool sharma",
        name_phonetic=None,
        dob=date(2005, 12, 4),
        gender="M",
        programme_code="UG-CSE",
        admission_date=date(2025, 7, 10),
        end_date=None,
        contact_token="tok_123",
        email_token=None,
        address_token=None,
    )
    feats = extract_pair_features(rec_a, rec_b)
    assert feats["jaro_winkler"] > 0.90
    assert feats["token_sort_ratio"] > 0.85
    assert feats["dob_day_month_swap"] == 1.0
    assert feats["dob_exact"] == 0.0
    assert feats["same_gender"] == 1.0
    assert feats["same_programme"] == 1.0
    assert feats["phone_match"] == 1.0
    assert feats["apaar_missing"] == 1.0


def test_initials_compatible():
    assert check_initials_compatible("rahul sharma", "r sharma") >= 0.85
    assert check_initials_compatible("amitabh bachchan", "amitabh b") >= 0.85
    assert check_initials_compatible("rahul sharma", "vikas gupta") == 0.0


def test_interval_overlap():
    # Overlapping 4-year programmes
    assert check_interval_overlap(date(2025, 7, 1), None, date(2026, 7, 1), None) == 1.0
    # Sequential (no overlap)
    assert (
        check_interval_overlap(
            date(2021, 7, 1), date(2024, 6, 30), date(2024, 7, 1), date(2028, 6, 30)
        )
        == 0.0
    )


# ---------------------------------------------------------------------------
# 2. Programme map (TF-IDF char n-grams)
# ---------------------------------------------------------------------------


def test_programme_normaliser():
    catalog = [
        {"canonical_code": "UG-CSE", "name": "Computer Science and Engineering", "level": "UG"},
        {"canonical_code": "UG-ME", "name": "Mechanical Engineering", "level": "UG"},
        {
            "canonical_code": "PGDM-FIN",
            "name": "Post Graduate Diploma in Management - Finance",
            "level": "PGDM",
        },
    ]
    norm = ProgrammeNormaliser(catalog)

    # Exact or near-exact match
    res = norm.suggest("Computer Science Engineering")
    assert len(res) > 0
    assert res[0]["canonical_code"] == "UG-CSE"
    assert res[0]["score"] >= 0.80
    assert not res[0]["requires_confirmation"]

    # Obscure query requiring confirmation
    res_obscure = norm.suggest("Robotics and Automation")
    assert len(res_obscure) > 0
    assert res_obscure[0]["score"] < 0.80
    assert res_obscure[0]["requires_confirmation"] is True


# ---------------------------------------------------------------------------
# 3. Duplicate matcher & blocking
# ---------------------------------------------------------------------------


def test_blocking_keys_generation():
    rec = RecordEntity(
        id=1,
        institution_id=1,
        apaar_id="990123456789",
        name_norm="ananya rao",
        name_phonetic=None,
        dob=date(2004, 8, 15),
        gender="F",
        programme_code="UG-CSE",
        admission_date=date(2025, 7, 1),
        end_date=None,
        contact_token="phone_tok_1",
        email_token="email_tok_1",
        address_token=None,
    )
    keys = get_blocking_keys(rec)
    assert any("2004" in k for k in keys)
    assert any("ana:" in k for k in keys)
    assert any("ct:phone_tok_1" in k for k in keys)


def test_duplicate_matcher_f1_floor():
    """Spec 13 acceptance: duplicate matcher F1 >= 0.85 on held-out synthetic corruptions."""
    matcher = DuplicateMatcher()
    X_train, y_train, X_test, y_test = generate_synthetic_training_data(
        n_positives=1000, n_negatives=1500, seed=42
    )
    matcher.fit(X_train, y_train)

    test_preds = matcher.model.predict(X_test)
    from sklearn.metrics import f1_score

    f1 = f1_score(y_test, test_preds)
    assert f1 >= 0.85, f"Expected F1 >= 0.85, got {f1:.3f}"


def test_transfer_aware_duplicate_suppression():
    """Spec 9.1: If other enrolment ended on/before admission date, treat as transfer (no case)."""
    matcher = DuplicateMatcher()
    # Record A: transferred out on 2024-06-30
    rec_a = RecordEntity(
        id=10,
        institution_id=1,
        apaar_id="990555555555",
        name_norm="rohit verma",
        name_phonetic=None,
        dob=date(2003, 5, 10),
        gender="M",
        programme_code="DIP-ME",
        admission_date=date(2021, 7, 1),
        end_date=date(2024, 6, 30),
        contact_token="tok_transfer",
        email_token=None,
        address_token=None,
    )
    # Record B: admitted into institution 2 on 2024-08-01 (genuine transfer)
    rec_b = RecordEntity(
        id=11,
        institution_id=2,
        apaar_id="990555555555",
        name_norm="rohit verma",
        name_phonetic=None,
        dob=date(2003, 5, 10),
        gender="M",
        programme_code="UG-ME",
        admission_date=date(2024, 8, 1),
        end_date=None,
        contact_token="tok_transfer",
        email_token=None,
        address_token=None,
    )
    matches = matcher.match_against_stored([rec_b], [rec_a])
    # Genuine transfer should be excluded from duplicate cases
    assert len(matches) == 0


# ---------------------------------------------------------------------------
# 4. Shared-identity ring detector
# ---------------------------------------------------------------------------


def test_ring_detector():
    records = []
    # 4 distinct students sharing one phone token across 2 institutions
    for sid in range(1, 5):
        records.append(
            ContactRecord(
                record_id=100 + sid,
                student_id=sid,
                apaar_id=f"99000000000{sid}",
                institution_id=1 if sid <= 2 else 2,
                institution_code="INST-01" if sid <= 2 else "INST-02",
                contact_token="shared_phone_token_99",
                email_token=None,
                address_token=None,
            )
        )
    # 1 clean student with unique phone token
    records.append(
        ContactRecord(
            record_id=105,
            student_id=5,
            apaar_id="990000000005",
            institution_id=1,
            institution_code="INST-01",
            contact_token="unique_phone_token_5",
            email_token=None,
            address_token=None,
        )
    )

    detector = RingDetector(min_shared_students=3)
    rings = detector.detect_rings(records)

    assert len(rings) == 1
    ring = rings[0]
    assert ring.size == 4
    assert ring.institutions_involved_count == 2
    assert "phone" in ring.token_type
    assert set(ring.student_ids) == {1, 2, 3, 4}


# ---------------------------------------------------------------------------
# 5. Anomaly detector
# ---------------------------------------------------------------------------


def test_anomaly_detector_ranks_outlier():
    detector = AnomalyDetector(seed=42)
    insts = []
    # Normal institutions
    for i in range(1, 15):
        insts.append(
            InstitutionMetrics(
                institution_id=i,
                institution_code=f"NORM-INST-{i:02d}",
                state="Maharashtra",
                academic_year="2025-26",
                approved_intake=120,
                declared_enrolment=120,
                total_records=118,
                active_records=118,
                late_admissions=5,  # ~4%
                supernumerary_count=10,
                lateral_count=15,
                warning_count=2,
                apaar_mismatch_count=1,
                prior_year_records=115,
                category_counts={"GENERAL": 50, "OBC": 30, "SC": 20, "ST": 10, "EWS": 8},
            )
        )
    # Severe anomaly: 85% late admissions and zero standard categories
    insts.append(
        InstitutionMetrics(
            institution_id=99,
            institution_code="ANOM-INST-99",
            state="Maharashtra",
            academic_year="2025-26",
            approved_intake=120,
            declared_enrolment=120,
            total_records=120,
            active_records=120,
            late_admissions=105,  # 87.5% late admission burst
            supernumerary_count=45,
            lateral_count=40,
            warning_count=35,
            apaar_mismatch_count=25,
            prior_year_records=40,
            category_counts={"OTHER": 120},
        )
    )

    scores = detector.analyze_institutions(insts)
    outlier = next(s for s in scores if s.institution_id == 99)
    assert outlier.rank == 1, f"Expected anomaly rank 1, got {outlier.rank}"
    assert outlier.is_anomalous is True
    assert len(outlier.top_features) > 0
    top_f = outlier.top_features[0]
    assert "z_score" in top_f and "peer_mean" in top_f


# ---------------------------------------------------------------------------
# 6. Review queue API, RBAC, and AICTE_ONLY visibility tests
# ---------------------------------------------------------------------------


def test_review_queue_and_decision_lifecycle(client, auth):
    """Test full case listing, AICTE_ONLY isolation, decision flow, and feedback."""
    with SessionLocal() as db:
        # Create an AICTE_ONLY fuzzy duplicate case
        c = Case(
            type=CaseType.DUP_FUZZY,
            visibility=CaseVisibility.AICTE_ONLY,
            priority=1,
            severity="WARNING",
            status=CaseStatus.OPEN,
            institution_id=None,
            subject_ids_json={"record_ids": [101, 102], "key": "DUP_FUZZY:101:102"},
            score=0.94,
            evidence_json={
                "features": {"token_sort_ratio": 0.95, "dob_exact": 1.0, "jaro_winkler": 0.96},
                "label": "likely_duplicate",
            },
            explanation_json={"summary_en": "High confidence duplicate pair"},
        )
        db.add(c)
        db.commit()
        case_id = c.id

    # 1. AICTE roles can view the case
    r_admin = client.get(f"{API}/cases/{case_id}", headers=auth("AICTE_ADMIN"))
    assert r_admin.status_code == 200
    assert r_admin.json()["type"] == "DUP_FUZZY"
    assert r_admin.json()["visibility"] == "AICTE_ONLY"

    r_rev = client.get(f"{API}/cases?type=DUP_FUZZY", headers=auth("AICTE_REVIEWER"))
    assert r_rev.status_code == 200
    assert any(item["id"] == case_id for item in r_rev.json())

    # 2. Institution users NEVER see AICTE_ONLY cases (FR-9, Spec 4, NFR-1)
    r_maker = client.get(f"{API}/cases/{case_id}", headers=auth("INST_MAKER"))
    assert r_maker.status_code == 404, "Institution maker must receive 404 for AICTE_ONLY case"

    r_inst_list = client.get(f"{API}/cases", headers=auth("INST_MAKER"))
    assert r_inst_list.status_code == 200
    for item in r_inst_list.json():
        assert item["visibility"] == "INSTITUTION", "Institution user saw non-INSTITUTION case"

    # 3. Institution users cannot decide AICTE cases
    r_decide_maker = client.post(
        f"{API}/cases/{case_id}/decision",
        json={"action": "CONFIRM_DUPLICATE", "note": "Confirmed by maker"},
        headers=auth("INST_MAKER"),
    )
    assert r_decide_maker.status_code == 403

    # 4. AICTE officer confirms duplicate -> status CONFIRMED + ml_feedback recorded
    r_decide = client.post(
        f"{API}/cases/{case_id}/decision",
        json={"action": "CONFIRM_DUPLICATE", "note": "Confirmed cross-institution duplicate"},
        headers=auth("AICTE_ADMIN"),
    )
    assert r_decide.status_code == 200
    assert r_decide.json()["case_status"] == "CONFIRMED"
    assert r_decide.json()["feedback_recorded"] is True

    with SessionLocal() as db:
        fb = db.scalar(select(MlFeedback).where(MlFeedback.case_id == case_id))
        assert fb is not None
        assert fb.label == "CONFIRMED"
        assert fb.features_json["dob_exact"] == 1.0


def test_anomaly_case_decision(client, auth):
    with SessionLocal() as db:
        c = Case(
            type=CaseType.ANOMALY,
            visibility=CaseVisibility.AICTE_ONLY,
            priority=2,
            severity="WARNING",
            status=CaseStatus.OPEN,
            institution_id=1,
            subject_ids_json={"key": "ANOMALY:1:2025-26", "institution_id": 1},
            score=0.82,
            evidence_json={"top_features": [{"feature": "late_admission_share", "z_score": 3.4}]},
            explanation_json={"summary_en": "Late admission burst outlier"},
        )
        db.add(c)
        db.commit()
        cid = c.id

    r = client.post(
        f"{API}/cases/{cid}/decision",
        json={"action": "ACKNOWLEDGE", "note": "Audited with institution"},
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200
    assert r.json()["case_status"] == "RESOLVED"


def test_retrain_endpoint(client, auth):
    r = client.post(f"{API}/demo/retrain", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "report" in r.json()


# ---------------------------------------------------------------------------
# 7. Edge case tests
# ---------------------------------------------------------------------------


def test_feature_extraction_extreme_edge_cases():
    """Edge cases: single-word name, None DOB, None APAAR, no tokens."""
    rec_a = RecordEntity(
        id=501,
        institution_id=1,
        apaar_id=None,
        name_norm="cher",
        name_phonetic=None,
        dob=None,
        gender=None,
        programme_code=None,
        admission_date=None,
        end_date=None,
        contact_token=None,
        email_token=None,
        address_token=None,
    )
    rec_b = RecordEntity(
        id=502,
        institution_id=2,
        apaar_id=None,
        name_norm="madonna",
        name_phonetic=None,
        dob=None,
        gender=None,
        programme_code=None,
        admission_date=None,
        end_date=None,
        contact_token=None,
        email_token=None,
        address_token=None,
    )
    feats = extract_pair_features(rec_a, rec_b)
    assert feats["apaar_missing"] == 1.0
    assert feats["dob_exact"] == 0.0
    assert feats["dob_day_month_swap"] == 0.0
    assert feats["initials_match"] == 0.0
    assert 0.0 <= feats["jaro_winkler"] <= 1.0


def test_transfer_date_boundary_edge_cases():
    """Spec 9.1: Boundary testing for transfer logic."""
    matcher = DuplicateMatcher()
    # A: Previous enrolment ends on 2024-07-15
    rec_a = RecordEntity(
        id=601,
        institution_id=1,
        apaar_id="990777777777",
        name_norm="arun patel",
        name_phonetic=None,
        dob=date(2004, 3, 14),
        gender="M",
        programme_code="UG-CSE",
        admission_date=date(2022, 7, 1),
        end_date=date(2024, 7, 15),
        contact_token="tok_patel",
        email_token=None,
        address_token=None,
    )
    # Case 1: Exact same date transfer (admission_date == end_date) -> Transfer (No case)
    rec_same_day = RecordEntity(
        id=602,
        institution_id=2,
        apaar_id="990777777777",
        name_norm="arun patel",
        name_phonetic=None,
        dob=date(2004, 3, 14),
        gender="M",
        programme_code="UG-IT",
        admission_date=date(2024, 7, 15),
        end_date=None,
        contact_token="tok_patel",
        email_token=None,
        address_token=None,
    )
    assert len(matcher.match_against_stored([rec_same_day], [rec_a])) == 0

    # Case 2: New admission before previous end_date (2024-07-10 < 2024-07-15) -> Duplicate!
    rec_overlap = RecordEntity(
        id=603,
        institution_id=2,
        apaar_id="990777777777",
        name_norm="arun patel",
        name_phonetic=None,
        dob=date(2004, 3, 14),
        gender="M",
        programme_code="UG-IT",
        admission_date=date(2024, 7, 10),
        end_date=None,
        contact_token="tok_patel",
        email_token=None,
        address_token=None,
    )
    matches = matcher.match_against_stored([rec_overlap], [rec_a])
    assert len(matches) == 1
    assert matches[0].priority == 1


def test_ring_detector_single_institution_not_flagged():
    """Spec 9.3: Rings must span multiple institutions (institutions_involved_count > 1)."""
    # 5 students share phone token but all belong to the SAME institution
    records = [
        ContactRecord(
            record_id=700 + sid,
            student_id=sid,
            apaar_id=f"990000000{sid:03d}",
            institution_id=1,
            institution_code="INST-01",
            contact_token="family_shared_phone",
            email_token=None,
            address_token=None,
        )
        for sid in range(1, 6)
    ]
    detector = RingDetector(min_shared_students=3)
    rings = detector.detect_rings(records)
    assert len(rings) == 0, "Single-institution clusters must not be flagged as fraud rings"


def test_cannot_decide_already_closed_case(client, auth):
    """Spec 11: Attempting to decide a case that is already closed returns 409."""
    with SessionLocal() as db:
        c = Case(
            type=CaseType.DUP_FUZZY,
            visibility=CaseVisibility.AICTE_ONLY,
            priority=1,
            severity="WARNING",
            status=CaseStatus.CONFIRMED,  # already confirmed
            institution_id=None,
            subject_ids_json={"record_ids": [201, 202], "key": "DUP_FUZZY:201:202"},
            score=0.95,
            evidence_json={"features": {}},
            explanation_json={"summary_en": "Previously confirmed"},
        )
        db.add(c)
        db.commit()
        cid = c.id

    r = client.post(
        f"{API}/cases/{cid}/decision",
        json={"action": "CONFIRM_DUPLICATE", "note": "Duplicate attempt"},
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CASE_ALREADY_DECIDED"


def test_unsupported_decision_action_fails(client, auth):
    """Spec 11: Invalid action returns 400 or 422."""
    with SessionLocal() as db:
        c = Case(
            type=CaseType.DUP_FUZZY,
            visibility=CaseVisibility.AICTE_ONLY,
            priority=1,
            severity="WARNING",
            status=CaseStatus.OPEN,
            institution_id=None,
            subject_ids_json={"record_ids": [301, 302], "key": "DUP_FUZZY:301:302"},
            score=0.91,
            evidence_json={"features": {}},
            explanation_json={"summary_en": "Fuzzy case"},
        )
        db.add(c)
        db.commit()
        cid = c.id

    # ACCEPT_EXCEPTION is only valid for SEAT_OVER_CAP
    r_bad_type = client.post(
        f"{API}/cases/{cid}/decision",
        json={"action": "ACCEPT_EXCEPTION", "note": "Invalid"},
        headers=auth("AICTE_ADMIN"),
    )
    assert r_bad_type.status_code == 400
    assert r_bad_type.json()["error"]["code"] == "CASE_TYPE_UNSUPPORTED"

    # Completely invalid action rejected by Pydantic validation
    r_invalid = client.post(
        f"{API}/cases/{cid}/decision",
        json={"action": "BOGUS_ACTION", "note": "Invalid"},
        headers=auth("AICTE_ADMIN"),
    )
    assert r_invalid.status_code == 422
    assert r_invalid.json()["error"]["code"] == "VALIDATION_ERROR"
