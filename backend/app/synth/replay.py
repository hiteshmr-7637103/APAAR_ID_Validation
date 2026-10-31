"""Replay the seeded national records through the real rule engine and Seat Ledger.

The generator writes historical records only (D25): no ledger rows, flags, validation
results or cases. This module runs every stored record through `validate_record()` and the
Seat Ledger (`seat_ledger.admit_many`, the same locking algorithm as `admit()`), in
admission-date order within each institution-programme bucket, so that:

- `seat_ledger_balances` / `seat_ledger_entries` hold every seat, and `over_cap` is set on
  the admissions beyond a bucket's cap, with one AICTE-only SEAT_OVER_CAP case per bucket;
- `validation_results` hold every rule outcome, and DUP_EXACT_CROSS / TIMELINE cases are
  opened (AICTE-only);
- records with no ERROR rise from T0 to T1, and those whose APAAR ID also matches the MOCK
  registry (name fuzzy + exact DOB) on to T2 (FR-18), each step logged in `trust_events`.

Seeded records keep their historical status (ACTIVE / WITHDRAWN / GRADUATED): they were
admitted before the system existed, so records with an ERROR stay stored at T0 and every
stored record holds its seat (withdrawals do not release seats retroactively, spec 7.3).

Run: `python -m app.synth.replay` (POST /demo/seed runs it automatically).
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt
from app.db.models import (
    Case,
    LedgerKey,
    RecordStatus,
    Severity,
    Student,
    StudentRecord,
    ValidationResult,
)
from app.services import seat_ledger, trust
from app.services.cases import bulk_case_rows
from app.services.quota import active_rules, ledger_key_for
from app.services.records import outcome_rows
from app.services.rule_engine import DbRuleContext, RecordInput, validate_record

_BATCH = 5000
NO_SEAT = frozenset({RecordStatus.REJECTED, RecordStatus.NEEDS_CORRECTION})


def _bulk(db: Session, model, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _BATCH):
        db.execute(insert(model), rows[i : i + _BATCH])


def replay_seeded_records(db: Session, today: date | None = None) -> dict[str, Any]:
    """Validate, seat and tier every stored record. Commits."""
    t0 = time.perf_counter()
    rules = active_rules(db)
    seat_ledger.ensure_balances(db, rules)
    ctx = DbRuleContext(db, today=today, rules=rules)
    ctx.prefetch_all()

    sr, st = StudentRecord, Student
    rows = db.execute(
        select(
            sr.id,
            sr.institution_id,
            sr.institution_programme_id,
            sr.student_college_id,
            sr.admission_date,
            sr.end_date,
            sr.admission_category,
            sr.entry_type,
            sr.mode,
            sr.nationality,
            sr.is_nri,
            sr.nri_sponsor_enc,
            sr.is_oci,
            sr.oci_number_enc,
            sr.is_foreign_national,
            sr.id_document_note_enc,
            sr.status,
            st.apaar_id,
            st.name_enc,
            st.dob,
            st.gender,
            sr.student_id,
        )
        .join(st, st.id == sr.student_id)
        .order_by(sr.institution_programme_id, sr.admission_date, sr.id)
    ).all()

    names: dict[int, str | None] = {}
    result_rows: list[dict] = []
    specs = []
    passed: list[tuple[int, int]] = []  # (record id, earned tier)
    codes: Counter = Counter()
    buckets: dict[tuple[int, LedgerKey], list[int]] = {}
    for r in rows:
        if r.student_id not in names:
            names[r.student_id] = decrypt(r.name_enc)
        inp = RecordInput(
            institution_id=r.institution_id,
            institution_programme_id=r.institution_programme_id,
            record_id=r.id,
            status=str(r.status),
            apaar_id=r.apaar_id,
            full_name=names[r.student_id],
            dob=r.dob,
            gender=str(r.gender) if r.gender else None,
            admission_date=r.admission_date,
            end_date=r.end_date,
            admission_category=str(r.admission_category),
            entry_type=str(r.entry_type),
            mode=str(r.mode),
            nationality=r.nationality,
            is_nri=r.is_nri,
            nri_sponsor=decrypt(r.nri_sponsor_enc),
            is_oci=r.is_oci,
            oci_number=decrypt(r.oci_number_enc),
            is_foreign_national=r.is_foreign_national,
            id_document_note=decrypt(r.id_document_note_enc),
            student_college_id=r.student_college_id,
        )
        inp.programme_code = ctx.ip_by_id(r.institution_programme_id).programme_code
        report = validate_record(inp, ctx)
        result_rows.extend(outcome_rows(report.outcomes, r.id))
        specs.extend(report.cases)
        codes.update(report.codes)
        if not report.has_error:
            passed.append((r.id, trust.earned_tier(False, report.apaar_status)))
        if r.status not in NO_SEAT:
            key = ledger_key_for(r.admission_category, r.entry_type)
            buckets.setdefault((r.institution_programme_id, key), []).append(r.id)
    t1 = time.perf_counter()

    # Seat Ledger: same lock-and-count algorithm as admit(), one bucket at a time.
    metas = seat_ledger.all_ip_meta(db)
    over_records = 0
    over_cases: set[int] = set()
    for (ip_id, key), ids in buckets.items():
        for res in seat_ledger.admit_many(db, metas[ip_id], key, ids, rules=rules):
            if res.over:
                over_records += 1
                over_cases.add(res.case_id)
                o = res.outcome()
                result_rows.extend(outcome_rows([o], res.record_id))
                codes[o.code] += 1
    t2 = time.perf_counter()

    case_rows = bulk_case_rows(specs)
    _bulk(db, Case, case_rows)
    _bulk(db, ValidationResult, result_rows)

    tiers = trust.bulk_apply(db, passed, actor_t1="system:replay")
    db.commit()
    t3 = time.perf_counter()

    severities = Counter(str(row["severity"]) for row in result_rows)
    return {
        "records": len(rows),
        "validation_results": {s.value: severities.get(s.value, 0) for s in Severity},
        "codes": dict(sorted(codes.items())),
        "cases": dict(sorted(Counter(str(c["type"]) for c in case_rows).items()))
        | {"SEAT_OVER_CAP": len(over_cases)},
        "over_cap_records": over_records,
        "trust_tiers": {
            "T0": len(rows) - len(passed),
            "T1": tiers.get(trust.T1, 0),
            "T2": tiers.get(trust.T2, 0),
        },
        "timings_s": {
            "rules": round(t1 - t0, 2),
            "ledger": round(t2 - t1, 2),
            "write": round(t3 - t2, 2),
        },
    }


def main() -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        if db.scalar(select(seat_ledger.SeatLedgerEntry.id).limit(1)) is not None:
            print("[replay] ledger already populated; run POST /demo/seed for a fresh replay")
            return
        print(f"[replay] {replay_seeded_records(db)}")


if __name__ == "__main__":
    main()
