"""Replay of the seeded national dataset through the rule engine and Seat Ledger, scored
against the generator's ground-truth labels (spec 10, 14: rule detection per injected
fault, no cases for genuine transfers, no false positives on clean records)."""

from __future__ import annotations

from collections import defaultdict

import pytest
from sqlalchemy import func, select

from app.db.models import (
    Case,
    CaseStatus,
    CaseType,
    GroundTruthLabel,
    SeatLedgerBalance,
    SeatLedgerEntry,
    Severity,
    StudentRecord,
    TrustEvent,
    ValidationResult,
)
from app.db.session import SessionLocal
from app.services.seat_ledger import case_key
from app.synth.loader import reset_generated_data
from tests.conftest import API

FAULT_CODE = {
    "APAAR_INVALID": "APAAR_INVALID",
    "APAAR_SUSPICIOUS": "APAAR_SUSPICIOUS",
    "APAAR_MISMATCH": "APAAR_MISMATCH",
    "DUP_EXACT_SAME_INST": "DUP_EXACT_SAME_INST",
    "OCI_NO_NUMBER": "OCI_NUMBER_MISSING",
    "FN_INDIAN_NATIONALITY": "FN_NATIONALITY_INVALID",
    "NRI_NO_SPONSOR": "NRI_SPONSOR_MISSING",
    "ADMISSION_OUTSIDE_WINDOW": "ADMISSION_OUTSIDE_WINDOW",
}


@pytest.fixture(scope="module")
def seeded(client, auth):
    r = client.post(f"{API}/demo/seed", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        labels = db.scalars(select(GroundTruthLabel)).all()
        results = defaultdict(set)
        for rid, code in db.execute(
            select(ValidationResult.student_record_id, ValidationResult.code)
        ):
            results[rid].add(code)
        cases = db.scalars(select(Case)).all()
        db.expunge_all()
    yield {"summary": r.json(), "labels": labels, "results": results, "cases": cases}
    with SessionLocal() as db:
        reset_generated_data(db)
        db.commit()


def _labels(seeded, scenario):
    return [lbl for lbl in seeded["labels"] if lbl.scenario == scenario]


def _groups(labels):
    out = defaultdict(list)
    for lbl in labels:
        out[lbl.group_id].append(lbl)
    return out


def _cases_with(seeded, record_ids, type_=None):
    ids = set(record_ids)
    return [
        c
        for c in seeded["cases"]
        if (type_ is None or c.type == type_)
        and ids & set(c.subject_ids_json.get("record_ids", []))
    ]


def test_seed_summary_reports_replay(seeded):
    replay = seeded["summary"]["replay"]
    assert replay["records"] == seeded["summary"]["student_records"]
    assert replay["over_cap_records"] > 0 and replay["cases"]["SEAT_OVER_CAP"] == 3
    assert "replay" in seeded["summary"]["timings_s"]


def test_every_record_holds_exactly_one_seat(seeded):
    with SessionLocal() as db:
        n = db.scalar(select(func.count()).select_from(StudentRecord))
        assert db.scalar(select(func.sum(SeatLedgerEntry.delta))) == n
        assert db.scalar(select(func.sum(SeatLedgerBalance.used))) == n
        per_record = db.execute(
            select(SeatLedgerEntry.student_record_id, func.sum(SeatLedgerEntry.delta)).group_by(
                SeatLedgerEntry.student_record_id
            )
        ).all()
    assert len(per_record) == n and {s for _, s in per_record} == {1}


def test_over_cap_matches_ground_truth_exactly(seeded):
    labels = _labels(seeded, "OVER_CAP")
    assert len(labels) == 3
    open_cases = [
        c
        for c in seeded["cases"]
        if c.type == CaseType.SEAT_OVER_CAP and c.status == CaseStatus.OPEN
    ]
    assert len(open_cases) == len(labels)  # no other bucket is over its cap
    with SessionLocal() as db:
        for lbl in labels:
            d = lbl.detail_json
            bal = db.get(SeatLedgerBalance, (lbl.institution_programme_id, d["bucket"]))
            assert (bal.cap, bal.used) == (d["cap"], d["used"]), d
            (case,) = [
                c
                for c in open_cases
                if c.subject_ids_json["key"] == case_key(lbl.institution_programme_id, d["bucket"])
            ]
            assert case.evidence_json["excess"] == d["excess"]
            assert case.visibility == "AICTE_ONLY"
            assert len(case.subject_ids_json["record_ids"]) == d["excess"]
            flagged = db.scalar(
                select(func.count()).where(
                    StudentRecord.institution_programme_id == lbl.institution_programme_id,
                    StudentRecord.ledger_key == d["bucket"],
                    StudentRecord.over_cap.is_(True),
                )
            )
            assert flagged == d["excess"]
        total_flagged = db.scalar(select(func.count()).where(StudentRecord.over_cap.is_(True)))
    assert total_flagged == sum(lbl.detail_json["excess"] for lbl in labels)


def test_genuine_transfers_raise_info_and_no_case(seeded):
    labels = _labels(seeded, "TRANSFER")
    assert len(labels) == 60  # 30 transfers, FROM + TO
    ids = [lbl.student_record_id for lbl in labels]
    assert _cases_with(seeded, ids) == []
    for lbl in labels:
        codes = seeded["results"][lbl.student_record_id]
        if lbl.role == "TO":
            assert codes == {"TRANSFER_DETECTED"}, codes
        else:
            assert codes == set(), codes


def test_same_apaar_duplicates_open_one_aicte_only_case_per_person(seeded):
    groups = _groups(_labels(seeded, "DUPLICATE"))
    same = {g: ls for g, ls in groups.items() if ls[0].detail_json["apaar_mode"] == "SAME"}
    cross_cases = [c for c in seeded["cases"] if c.type == CaseType.DUP_EXACT_CROSS]
    assert same and len(cross_cases) == len(same)
    for ls in same.values():
        ids = {lbl.student_record_id for lbl in ls}
        (case,) = _cases_with(seeded, ids, CaseType.DUP_EXACT_CROSS)
        assert set(case.subject_ids_json["record_ids"]) == ids
        assert case.visibility == "AICTE_ONLY"
        for lbl in ls:
            codes = seeded["results"][lbl.student_record_id]
            if lbl.role == "COPY":
                assert "ENROLMENT_ELSEWHERE_NEUTRAL" in codes
    for ls in groups.values():
        mode = ls[0].detail_json["apaar_mode"]
        for lbl in ls:
            if lbl.role == "COPY" and mode == "MISSING":
                assert "APAAR_INVALID" in seeded["results"][lbl.student_record_id]
            if lbl.role == "COPY" and mode == "WRONG":
                assert "APAAR_NOT_FOUND" in seeded["results"][lbl.student_record_id]


def test_timeline_scenarios_are_detected(seeded):
    groups = _groups(_labels(seeded, "TIMELINE"))
    assert groups
    for group, ls in groups.items():
        ids = {lbl.student_record_id for lbl in ls}
        kind = ls[0].corruption_types[0]
        codes = set().union(*(seeded["results"][i] for i in ids))
        if kind in ("TIMELINE_OVERLAP", "TIMELINE_TOO_SHORT"):
            (case,) = _cases_with(seeded, ids, CaseType.TIMELINE)
            assert case.evidence_json["code"] == kind and case.visibility == "AICTE_ONLY"
            assert set(case.subject_ids_json["record_ids"]) == ids
        if kind == "TIMELINE_TOO_SHORT":
            assert "TIMELINE_TOO_SHORT" in codes  # on the too-short diploma itself
        if kind in ("TIMELINE_LATERAL_NO_PRIOR", "TIMELINE_INVALID_DATE"):
            assert kind in codes, (group, codes)


def test_rule_faults_are_detected(seeded):
    labels = _labels(seeded, "RULE_FAULT")
    assert len(labels) == 40
    for lbl in labels:
        code = FAULT_CODE[lbl.corruption_types[0]]
        assert code in seeded["results"][lbl.student_record_id], (lbl.group_id, code)


def test_no_false_positives_on_clean_records(seeded):
    """Records outside every injected scenario get no ERROR/WARNING (over-cap flags only
    land in the labelled over-cap buckets); registry spelling/script variants still match."""
    labelled = {lbl.student_record_id for lbl in seeded["labels"] if lbl.student_record_id}
    with SessionLocal() as db:
        noisy = db.execute(
            select(ValidationResult.student_record_id, ValidationResult.code).where(
                ValidationResult.severity != Severity.INFO,
                ValidationResult.code != "SEAT_OVER_CAP",
            )
        ).all()
    unexpected = [(rid, code) for rid, code in noisy if rid not in labelled]
    # Lateral entrants' generated prior diplomas are unlabelled history and must be clean too.
    assert unexpected == []
    for lbl in _labels(seeded, "REGISTRY_VARIANT"):
        assert "APAAR_MISMATCH" not in seeded["results"][lbl.student_record_id]
    assert seeded["summary"]["replay"]["codes"]["APAAR_MISMATCH"] == 5


def test_trust_tiers_follow_errors(seeded):
    with SessionLocal() as db:
        err_ids = set(
            db.scalars(
                select(ValidationResult.student_record_id).where(
                    ValidationResult.severity == Severity.ERROR
                )
            )
        )
        mismatch_ids = set(
            db.scalars(
                select(ValidationResult.student_record_id).where(
                    ValidationResult.code.in_(["APAAR_MISMATCH", "APAAR_NOT_FOUND"])
                )
            )
        )
        tiers = dict(db.execute(select(StudentRecord.id, StudentRecord.trust_tier)).all())
        events = db.scalar(select(func.count()).select_from(TrustEvent))
    assert {tiers[i] for i in err_ids} == {0}
    # Phase 4: T2 = registry match (name fuzzy + exact DOB); otherwise T1 (FR-18).
    passed = {i: t for i, t in tiers.items() if i not in err_ids}
    assert set(passed.values()) == {1, 2}
    assert {passed[i] for i in mismatch_ids if i in passed} <= {1}
    t2 = sum(1 for t in passed.values() if t == 2)
    assert t2 > 0.9 * len(passed)
    assert events == len(passed) + t2  # one event per step: T0->T1, T1->T2
    summary = seeded["summary"]["replay"]["trust_tiers"]
    assert summary == {"T0": len(err_ids), "T1": len(passed) - t2, "T2": t2}


def test_institution_users_see_no_replayed_cases(client, auth, seeded):
    assert all(c.visibility == "AICTE_ONLY" for c in seeded["cases"])
    for who in ("INST_MAKER", "INST_CHECKER", "OTHER_MAKER"):
        assert client.get(f"{API}/cases", headers=auth(who)).json() == []
    assert len(client.get(f"{API}/cases?limit=1000", headers=auth("AICTE_REVIEWER")).json()) == (
        len(seeded["cases"])
    )
