"""Seat Ledger against PostgreSQL (spec 7.3, 13): concurrency, buckets, flag-not-reject,
release, rules-as-data recompute."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Case,
    CaseStatus,
    CaseType,
    CaseVisibility,
    LedgerKey,
    QuotaRule,
    SeatLedgerBalance,
    SeatLedgerEntry,
    StudentRecord,
)
from app.db.session import SessionLocal
from app.services import seat_ledger
from app.services.quota import default_rule_values
from app.synth.loader import reset_generated_data
from tests.conftest import TEST_DATABASE_URL
from tests.factories import drop_test_institutions, make_institution, make_ip, make_record


@pytest.fixture(scope="module")
def inst():
    with SessionLocal() as db:
        reset_generated_data(db)
        i = make_institution(db, "LEDGER")
        db.commit()
        inst_id = i.id
    yield inst_id
    with SessionLocal() as db:
        db.execute(delete(QuotaRule))
        drop_test_institutions(db)
        reset_generated_data(db)
        db.commit()


_ip_seq = iter(range(1, 10_000))


def _ip(inst_id, prog="UG-CSE", intake=20, shift="REGULAR"):
    """A fresh institution-programme row (own test institution, so keys never collide)."""
    with SessionLocal() as db:
        ip = make_ip(db, make_institution(db, f"LEDGER-{next(_ip_seq)}"), prog, intake, shift)
        db.commit()
        return ip.id


def _records(ip_id, n, category="GENERAL", entry="REGULAR"):
    from app.db.models import InstitutionProgramme

    with SessionLocal() as db:
        ip = db.get(InstitutionProgramme, ip_id)
        ids = [make_record(db, ip, category, entry) for _ in range(n)]
        db.commit()
        return ids


def _admit_all(ids):
    with SessionLocal() as db:
        out = [seat_ledger.admit(db, rid) for rid in ids]
        db.commit()
        return out


def _balance(ip_id, key=LedgerKey.APPROVED):
    with SessionLocal() as db:
        b = db.get(SeatLedgerBalance, (ip_id, key))
        return (b.used, b.cap) if b else (0, None)


def _open_cases(ip_id, key=LedgerKey.APPROVED):
    with SessionLocal() as db:
        return db.scalars(
            select(Case).where(
                Case.type == CaseType.SEAT_OVER_CAP,
                Case.status == CaseStatus.OPEN,
                Case.subject_ids_json["key"].astext == seat_ledger.case_key(ip_id, key),
            )
        ).all()


def _flags(ids):
    with SessionLocal() as db:
        return dict(
            db.execute(
                select(StudentRecord.id, StudentRecord.over_cap).where(StudentRecord.id.in_(ids))
            ).all()
        )


# --------------------------------------------------------------------------- concurrency


def test_concurrent_admissions_exactly_remaining_seats_clean(inst):
    """Spec 13: bucket with 5 seats left, 50 parallel admissions -> exactly 5 clean and 45
    flagged, used = initial + 50, no lost updates, one SEAT_OVER_CAP case with the right
    excess; releasing 3 records lowers the excess by 3."""
    ip_id = _ip(inst, intake=20)
    initial = _records(ip_id, 15)
    _admit_all(initial)
    assert _balance(ip_id) == (15, 20)  # 5 seats left

    racers = _records(ip_id, 50)
    engine = create_engine(TEST_DATABASE_URL, pool_size=50, max_overflow=5)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = threading.Barrier(len(racers))

    def admit(rid):
        with Session() as db:
            barrier.wait()  # release all 50 transactions at once
            res = seat_ledger.admit(db, rid)
            db.commit()
            return res

    try:
        with ThreadPoolExecutor(max_workers=len(racers)) as pool:
            results = list(pool.map(admit, racers))
    finally:
        engine.dispose()

    clean = [r for r in results if not r.over]
    flagged = [r for r in results if r.over]
    assert len(clean) == 5 and len(flagged) == 45
    # no lost updates: every admission saw a distinct running count 16..65
    assert sorted(r.used for r in results) == list(range(16, 66))
    assert {r.used for r in clean} == set(range(16, 21))
    used, cap = _balance(ip_id)
    assert (used, cap) == (15 + 50, 20)
    with SessionLocal() as db:
        total = db.scalar(
            select(func.sum(SeatLedgerEntry.delta)).where(
                SeatLedgerEntry.institution_programme_id == ip_id
            )
        )
        n_entries = db.scalar(
            select(func.count()).where(SeatLedgerEntry.institution_programme_id == ip_id)
        )
    assert total == used == 65 and n_entries == 65
    flags = _flags(initial + racers)
    assert sum(flags.values()) == 45 and not any(flags[i] for i in initial)
    assert {r.record_id for r in flagged} == {i for i in racers if flags[i]}

    cases = _open_cases(ip_id)
    assert len(cases) == 1
    case = cases[0]
    assert case.visibility == CaseVisibility.AICTE_ONLY
    assert case.evidence_json["excess"] == 45 and case.evidence_json["used"] == 65
    assert case.evidence_json["cap"] == 20 and case.evidence_json["bucket"] == "APPROVED"
    assert sorted(case.subject_ids_json["record_ids"]) == sorted(r.record_id for r in flagged)

    # Release 3 flagged records, also in parallel.
    to_release = [r.record_id for r in flagged[:3]]
    engine = create_engine(TEST_DATABASE_URL, pool_size=5)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def release(rid):
        with Session() as db:
            res = seat_ledger.release(db, rid)
            db.commit()
            return res

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(release, to_release))
    finally:
        engine.dispose()
    assert _balance(ip_id) == (62, 20)
    case = _open_cases(ip_id)[0]
    assert case.evidence_json["excess"] == 42  # lowered by exactly 3
    assert sum(_flags(initial + racers).values()) == 42
    assert not set(to_release) & set(case.subject_ids_json["record_ids"])


def test_release_is_idempotent_and_releasing_a_clean_seat_promotes_the_earliest_flag(inst):
    ip_id = _ip(inst, intake=3)
    ids = _records(ip_id, 5)
    _admit_all(ids)
    assert _flags(ids) == {**dict.fromkeys(ids[:3], False), **dict.fromkeys(ids[3:], True)}
    with SessionLocal() as db:
        assert seat_ledger.release(db, ids[0]) is not None  # a clean seat
        assert seat_ledger.release(db, ids[0]) is None  # nothing left to release
        db.commit()
    flags = _flags(ids)
    assert flags[ids[3]] is False and flags[ids[4]] is True  # flagged count == excess
    assert _balance(ip_id) == (4, 3)
    assert _open_cases(ip_id)[0].evidence_json["excess"] == 1
    with SessionLocal() as db:
        seat_ledger.release(db, ids[4])
        db.commit()
    assert _balance(ip_id) == (3, 3)
    assert _open_cases(ip_id) == []  # closed automatically when used <= cap
    with SessionLocal() as db:
        closed = db.scalars(
            select(Case).where(
                Case.subject_ids_json["key"].astext == seat_ledger.case_key(ip_id, "APPROVED")
            )
        ).all()
    assert [c.status for c in closed] == [CaseStatus.RESOLVED]


# --------------------------------------------------------------------------- buckets


def test_120_regular_plus_5_fn_is_clean(inst):
    ip_id = _ip(inst, intake=120)
    results = _admit_all(_records(ip_id, 120) + _records(ip_id, 5, "FN"))
    assert not any(r.over for r in results)
    assert _balance(ip_id, LedgerKey.APPROVED) == (120, 120)
    assert _balance(ip_id, LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC) == (5, 18)  # floor(15%)
    assert _open_cases(ip_id) == []


def test_125_regular_flags_approved_by_5(inst):
    ip_id = _ip(inst, intake=120)
    results = _admit_all(_records(ip_id, 125))
    assert [r.over for r in results] == [False] * 120 + [True] * 5
    assert _balance(ip_id) == (125, 120)
    (case,) = _open_cases(ip_id)
    assert case.evidence_json["excess"] == 5 and case.evidence_json["bucket"] == "APPROVED"
    outcome = results[-1].outcome()
    assert outcome.code == "SEAT_OVER_CAP" and outcome.severity == "WARNING"
    assert outcome.evidence == {"bucket": "APPROVED", "cap": 120, "used": 125, "excess": 5}
    assert "flagged and held for review" in outcome.message_en


def test_lateral_tfw_nri_and_second_shift_buckets(inst):
    ip_id = _ip(inst, intake=120)
    lateral = _admit_all(_records(ip_id, 25, entry="LATERAL"))
    assert all(r.ledger_key == LedgerKey.LATERAL for r in lateral)
    assert sum(r.over for r in lateral) == 1  # cap ceil(120 * 0.20) = 24
    tfw = _admit_all(_records(ip_id, 7, "TFW"))
    assert sum(r.over for r in tfw) == 1  # cap floor(120 * 0.05) = 6
    nri = _admit_all(_records(ip_id, 1, "NRI"))
    assert nri[0].over and nri[0].cap == 0  # default NRI cap 0: unapproved NRI flagged
    fn_lateral = _admit_all(_records(ip_id, 1, "FN", "LATERAL"))
    assert fn_lateral[0].ledger_key == LedgerKey.LATERAL  # LATERAL wins
    assert _balance(ip_id, LedgerKey.APPROVED)[0] == 0  # nothing leaked into APPROVED
    second = _ip(inst, intake=60, shift="SECOND")
    fn_second = _admit_all(_records(second, 1, "FN"))
    assert fn_second[0].cap == 0 and fn_second[0].over  # supernumerary is REGULAR shift only
    with SessionLocal() as db:
        keys = {
            c.subject_ids_json["ledger_key"]
            for c in db.scalars(select(Case).where(Case.type == CaseType.SEAT_OVER_CAP))
            if c.subject_ids_json["institution_programme_id"] == ip_id
        }
    assert keys == {"LATERAL", "SUPERNUMERARY_TFW", "NRI"}


def test_flag_not_reject_stores_every_over_cap_record(inst):
    ip_id = _ip(inst, intake=2)
    ids = _records(ip_id, 6)
    _admit_all(ids)
    with SessionLocal() as db:
        stored = db.scalars(select(StudentRecord).where(StudentRecord.id.in_(ids))).all()
    assert len(stored) == 6  # nothing dropped
    assert all(r.status == "PENDING_APPROVAL" for r in stored)
    assert sum(r.over_cap for r in stored) == 4
    assert _open_cases(ip_id)[0].evidence_json["excess"] == 4


def test_rule_change_recomputes_caps_and_flags(inst):
    """Rules-as-data (P21): a new LATERAL percentage changes caps, flags and the case."""
    ip_id = _ip(inst, intake=60)
    ids = _records(ip_id, 14, entry="LATERAL")
    _admit_all(ids)  # cap ceil(60 * 0.2) = 12 -> 2 flagged
    assert _open_cases(ip_id, LedgerKey.LATERAL)[0].evidence_json["excess"] == 2
    rules = default_rule_values()
    try:
        with SessionLocal() as db:
            db.add(
                QuotaRule(
                    key="bucket.LATERAL",
                    value_json={**rules["bucket.LATERAL"], "pct": 0.25},
                    version=2,
                )
            )
            db.flush()
            summary = seat_ledger.recompute_caps(db)
            db.commit()
        assert summary["caps_changed"] >= 1
        assert _balance(ip_id, LedgerKey.LATERAL) == (14, 15)
        assert not any(_flags(ids).values())
        assert _open_cases(ip_id, LedgerKey.LATERAL) == []
        with SessionLocal() as db:
            db.add(
                QuotaRule(
                    key="bucket.LATERAL",
                    value_json={**rules["bucket.LATERAL"], "pct": 0.10},
                    version=3,
                )
            )
            db.flush()
            seat_ledger.recompute_caps(db)
            db.commit()
        assert _balance(ip_id, LedgerKey.LATERAL) == (14, 6)
        flags = _flags(ids)
        assert [flags[i] for i in ids] == [False] * 6 + [True] * 8  # earliest seats stay clean
        assert _open_cases(ip_id, LedgerKey.LATERAL)[0].evidence_json["excess"] == 8
    finally:
        with SessionLocal() as db:
            db.execute(delete(QuotaRule).where(QuotaRule.key == "bucket.LATERAL"))
            seat_ledger.recompute_caps(db)
            db.commit()
