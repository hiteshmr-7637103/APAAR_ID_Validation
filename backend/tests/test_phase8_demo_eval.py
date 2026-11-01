"""Phase 8: Demo mode, fraud injection, concurrency demo, tamper audit, evaluation module.

Acceptance criteria (spec Section 14, 15):
- Live cases wired into fraud-injection scenarios (POST /demo/inject/{scenario}).
- Cross-institution duplicate creates review case.
- Genuine transfer evaluates as INFO and opens 0 review cases (transfer-awareness).
- Shared-phone ring creates RING case.
- Late admission burst triggers anomaly triage.
- Seat over-cap opens SEAT_OVER_CAP case.
- Concurrency demo endpoint: bucket with 5 seats left, 50 parallel admissions.
- Tamper audit helper: flags tampered row at exact index and restores chain.
- Evaluation module: returns non-empty metrics across all 7 criteria and writes docs.
- Rules-as-data admin (GET /rules, PUT /rules/{key}).
- RBAC: admin only for demo/evaluation endpoints.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import or_

from app.db.models import (
    Case,
    CaseType,
    Institution,
    InstitutionProgramme,
    SeatLedgerBalance,
    SeatLedgerEntry,
    Student,
    StudentRecord,
)
from app.db.session import SessionLocal

API = "/api/v1"
DOCS_EVAL_MD = pathlib.Path(__file__).resolve().parents[2] / "docs" / "evaluation.md"


@pytest.fixture(autouse=True)
def cleanup_demo_test_data():
    """Clean up any ephemeral demo institutions created during tests so test isolation holds."""
    yield
    with SessionLocal() as db:
        insts = (
            db.query(Institution)
            .filter(or_(Institution.code.like("CONC_%"), Institution.code.like("HEADCOUNT_%")))
            .all()
        )
        for inst in insts:
            ips = db.query(InstitutionProgramme).filter_by(institution_id=inst.id).all()
            for ip in ips:
                recs = db.query(StudentRecord).filter_by(institution_programme_id=ip.id).all()
                for r in recs:
                    st_id = r.student_id
                    db.delete(r)
                    if st_id:
                        st = db.get(Student, st_id)
                        if st:
                            db.delete(st)
                db.query(SeatLedgerEntry).filter_by(institution_programme_id=ip.id).delete()
                db.query(SeatLedgerBalance).filter_by(institution_programme_id=ip.id).delete()
                db.delete(ip)
            db.query(Case).filter_by(institution_id=inst.id).delete()
            db.delete(inst)
        db.commit()


# --------------------------------------------------------------------------- 1. Scenarios Listing
def test_demo_scenarios_list(client, auth):
    r = client.get(f"{API}/demo/scenarios", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    scenarios = r.json()
    assert isinstance(scenarios, list)
    ids = {s["id"] for s in scenarios}
    assert "cross_duplicate" in ids
    assert "genuine_transfer" in ids
    assert "shared_phone_ring" in ids
    assert "late_admission_burst" in ids
    assert "seat_over_cap" in ids
    assert "headcount_only" in ids


# --------------------------------------------------------------------------- 2. Fraud Injection
def test_inject_cross_duplicate(client, auth):
    r = client.post(f"{API}/demo/inject/cross_duplicate", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scenario"] == "cross_duplicate"
    assert "apaar_id" in data
    assert len(data["record_ids"]) == 2
    assert data["cases_opened"] >= 1


def test_inject_genuine_transfer_transfer_aware(client, auth):
    r = client.post(f"{API}/demo/inject/genuine_transfer", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scenario"] == "genuine_transfer"
    assert data["is_transfer"] is True
    # Transfer-aware: genuine transfer produces NO case
    assert data["cases_opened"] == 0


def test_inject_shared_phone_ring(client, auth):
    r = client.post(f"{API}/demo/inject/shared_phone_ring", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scenario"] == "shared_phone_ring"
    assert data["records_created"] == 4
    assert "shared_contact_token" in data


def test_inject_late_admission_burst(client, auth):
    r = client.post(f"{API}/demo/inject/late_admission_burst", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scenario"] == "late_admission_burst"
    assert data["records_injected"] == 15


def test_inject_seat_over_cap(client, auth):
    r = client.post(f"{API}/demo/inject/seat_over_cap", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scenario"] == "seat_over_cap"
    assert data["records_injected"] == 3
    assert "case_id" in data

    with SessionLocal() as db:
        case = db.get(Case, data["case_id"])
        assert case.type == CaseType.SEAT_OVER_CAP
        assert case.evidence_json["excess"] == 3


def test_inject_headcount_only(client, auth):
    r = client.post(f"{API}/demo/inject/headcount_only", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scenario"] == "headcount_only"
    assert data["coverage_band"] == "RED"
    assert data["coverage_pct"] == 0.0


# --------------------------------------------------------------------------- 3. Concurrency Test
def test_demo_concurrency_endpoint(client, auth):
    r = client.post(f"{API}/demo/concurrency-test", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "PASSED"
    assert data["seats_remaining_before_race"] == 5
    assert data["concurrent_attempts"] == 50
    # Exactly remaining seats clean, rest flagged over-cap
    assert data["clean_admissions"] == 5
    assert data["flagged_over_cap"] == 45
    assert data["final_used"] == 65
    assert data["no_lost_updates"] is True


# --------------------------------------------------------------------------- 4. Tamper Audit Helper
def test_demo_tamper_audit_endpoint(client, auth):
    r = client.post(f"{API}/demo/tamper-audit", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "SUCCESS", str(data)
    assert data["tamper_detected"] is True
    assert data["repaired_and_restored"] is True
    assert data["chain_valid_now"] is True
    assert data["first_broken_detected"]["reason"] == "hash_mismatch"


# --------------------------------------------------------------------------- 5. Evaluation Module
def test_evaluation_run_and_report_generation(client, auth):
    r = client.post(f"{API}/evaluation/run", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()

    # Verify all 7 criteria exist and contain non-empty metrics
    assert "disclaimer" in data
    assert "synthetic benchmark" in data["disclaimer"]

    # 1. Duplicate matcher (in-dist and held-out)
    dm = data["duplicate_matcher"]
    assert dm["in_distribution"]["f1"] >= 0.85
    assert dm["in_distribution"]["precision"] > 0
    assert dm["in_distribution"]["recall"] > 0
    assert dm["in_distribution"]["pr_auc"] > 0
    assert dm["held_out"]["f1"] > 0
    assert "calibration_error" in dm["in_distribution"]

    # 2. Rule engine
    re = data["rule_engine"]
    assert re["fault_detection_rate_pct"] > 50
    assert "INVALID_APAAR_CHECKSUM" in re["faults"]

    # 3. Anomaly & Rings
    ar = data["anomaly_and_rings"]
    assert ar["anomaly_detector"]["top_10_percent_ranking_rate_pct"] >= 80.0
    assert ar["ring_detector"]["ring_detection_recall_pct"] == 100.0

    # 4. Concurrency
    conc = data["seat_ledger_concurrency"]
    assert conc["clean_admissions_count"] == 5
    assert conc["flagged_over_cap_count"] == 45

    # 5. Performance
    perf = data["performance"]
    assert perf["single_record_validation_p50_ms"] < 100
    assert perf["projected_throughput_rows_per_sec"] > 0

    # 6. Review time saved
    rev = data["review_time_saved"]
    assert "stated_assumption" in rev
    assert rev["automated_clearance_rate_pct"] > 0

    # Verify docs/evaluation.md was written
    assert DOCS_EVAL_MD.exists()
    content = DOCS_EVAL_MD.read_text(encoding="utf-8")
    assert "# System Evaluation & Benchmark Report" in content
    assert "Synthetic Benchmark Notice" in content
    assert "Duplicate Matcher Performance" in content


def test_get_evaluation(client, auth):
    r = client.get(f"{API}/evaluation", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert "duplicate_matcher" in data
    assert "seat_ledger_concurrency" in data


# --------------------------------------------------------------------------- 6. Rules Admin
def test_rules_admin_list_and_update(client, auth):
    # List rules
    r = client.get(f"{API}/rules", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    rules = r.json()
    assert isinstance(rules, list)
    assert len(rules) >= 1

    # Update LATERAL quota rule
    rule_key = "bucket.LATERAL"
    r = client.put(
        f"{API}/rules/{rule_key}",
        json={
            "value_json": {"pct": 0.25, "rounding": "ceil", "verify": True},
            "description": "Test rule modification for audit",
        },
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["key"] == rule_key
    assert updated["value_json"]["pct"] == 0.25


# --------------------------------------------------------------------------- 7. RBAC Protection
def test_demo_and_evaluation_rbac_forbidden(client, auth):
    # Institution maker should be denied access to demo injection and evaluation
    r = client.post(f"{API}/demo/inject/cross_duplicate", headers=auth("INST_MAKER"))
    assert r.status_code == 403

    r = client.post(f"{API}/demo/concurrency-test", headers=auth("INST_MAKER"))
    assert r.status_code == 403

    r = client.post(f"{API}/evaluation/run", headers=auth("INST_MAKER"))
    assert r.status_code == 403

    # Student denied access
    r = client.post(f"{API}/demo/tamper-audit", headers=auth("STUDENT"))
    assert r.status_code == 403
