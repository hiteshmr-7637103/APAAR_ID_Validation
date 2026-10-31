"""Seat Ledger in flag mode (spec 7.3, FR-10, FR-11, P4, P24).

Every admission is a transaction against exactly one bucket of its institution-programme:

    BEGIN;
    SELECT used, cap FROM seat_ledger_balances
      WHERE institution_programme_id = :ip AND ledger_key = :key FOR UPDATE;
    used := used + 1;  over := (used > cap);
    INSERT seat_ledger_entries(delta=+1, ...);
    UPDATE seat_ledger_balances SET used = used;
    UPDATE student_records SET over_cap = :over;
    IF :over THEN upsert case SEAT_OVER_CAP (AICTE_ONLY) with bucket, cap, used, excess;
    COMMIT;            -- the caller commits

Nothing is ever rejected here: an over-cap admission is stored, flagged and held for an
AICTE officer. `release()` (rejection, correction, NEEDS_CORRECTION) takes the same lock,
writes a -1 delta, and refreshes or closes the open case.

Flag invariant: in each bucket the seat holders are ordered by when they took their seat;
the first `cap` are clean and the rest carry `over_cap = true`, so the number of flagged
records always equals `excess = max(0, used - cap)`. A release or a cap change re-derives
the flags under the lock (releasing a clean seat lets the earliest flagged record move
within the cap).

Caps come from the active `quota_rules` (`services/quota.py`): changing a rule and calling
`recompute_caps()` updates every balance with no code change. Withdrawals and graduations
do not release seats retroactively (spec 7.3 simplification).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.models import (
    Case,
    CaseStatus,
    CaseType,
    CaseVisibility,
    Institution,
    InstitutionProgramme,
    LedgerKey,
    Notification,
    Programme,
    RecordStatus,
    Role,
    SeatLedgerBalance,
    SeatLedgerEntry,
    Severity,
    StudentRecord,
    User,
)
from app.services.quota import active_rules, compute_cap, ip_natural_key, ledger_key_for
from app.services.rule_engine import Outcome, outcome

# Bucket mapping, exactly as spec 7.3 (first match wins):
#   entry_type == LATERAL -> LATERAL; category in FN/OCI/PIO/CIWGC -> SUPERNUMERARY_...;
#   TFW -> SUPERNUMERARY_TFW; NRI -> NRI; otherwise APPROVED.
bucket_for = ledger_key_for

B = SeatLedgerBalance


@dataclass(frozen=True)
class IPMeta:
    id: int
    institution_id: int
    institution_code: str
    programme_code: str
    academic_year: str
    shift: str
    approved_intake: int

    @property
    def natural_key(self) -> str:
        return ip_natural_key(
            self.institution_code, self.programme_code, self.academic_year, self.shift
        )


@dataclass
class SeatResult:
    record_id: int
    institution_programme_id: int
    ledger_key: LedgerKey
    cap: int
    used: int
    over: bool
    case_id: int | None
    meta: IPMeta

    @property
    def excess(self) -> int:
        return max(0, self.used - self.cap)

    def outcome(self) -> Outcome | None:
        """Institution-facing WARNING with its own numbers (FR-10)."""
        if not self.over:
            return None
        return over_cap_outcome(self.meta, self.ledger_key, self.cap, self.used)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_key": str(self.ledger_key),
            "cap": self.cap,
            "used": self.used,
            "excess": self.excess,
            "over_cap": self.over,
        }


def over_cap_outcome(meta: IPMeta, key: LedgerKey, cap: int, used: int) -> Outcome:
    excess = max(0, used - cap)
    return outcome(
        "SEAT_OVER_CAP",
        "admission_category",
        bucket=str(key),
        programme=meta.programme_code,
        year=meta.academic_year,
        cap=cap,
        used=used,
        excess=excess,
        evidence={"bucket": str(key), "cap": cap, "used": used, "excess": excess},
    )


# --------------------------------------------------------------------------- lookups


def _meta_query():
    return (
        select(
            InstitutionProgramme.id,
            InstitutionProgramme.institution_id,
            Institution.code,
            Programme.canonical_code,
            InstitutionProgramme.academic_year,
            InstitutionProgramme.shift,
            InstitutionProgramme.approved_intake,
        )
        .join(Institution, Institution.id == InstitutionProgramme.institution_id)
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
    )


def _to_meta(row) -> IPMeta:
    return IPMeta(row[0], row[1], row[2], row[3], row[4], str(row[5]), row[6])


def ip_meta(db: Session, ip_id: int) -> IPMeta:
    return _to_meta(db.execute(_meta_query().where(InstitutionProgramme.id == ip_id)).one())


def all_ip_meta(db: Session) -> dict[int, IPMeta]:
    return {row[0]: _to_meta(row) for row in db.execute(_meta_query())}


def cap_for(meta: IPMeta, key: LedgerKey, rules: dict[str, Any]) -> int:
    return compute_cap(LedgerKey(key), meta.approved_intake, meta.shift, meta.natural_key, rules)


def projection(db: Session, meta: IPMeta, key: LedgerKey, rules: dict[str, Any]) -> dict:
    """Read-only preview of one more admission into a bucket (no lock, no write); used by
    the dry-run validate endpoint."""
    cap = cap_for(meta, key, rules)
    used = db.scalar(
        select(B.used).where(B.institution_programme_id == meta.id, B.ledger_key == key)
    )
    used = used or 0
    after = used + 1
    return {
        "ledger_key": str(key),
        "cap": cap,
        "used": used,
        "used_after": after,
        "excess_after": max(0, after - cap),
        "would_be_flagged": after > cap,
    }


# --------------------------------------------------------------------------- locking core


def _lock_balance(db: Session, ip_id: int, key: LedgerKey, cap: int) -> tuple[int, int]:
    """SELECT used, cap ... FOR UPDATE (creating the balance row on first use)."""
    stmt = (
        select(B.used, B.cap)
        .where(B.institution_programme_id == ip_id, B.ledger_key == key)
        .with_for_update()
    )
    row = db.execute(stmt).first()
    if row is None:
        db.execute(
            pg_insert(B)
            .values(institution_programme_id=ip_id, ledger_key=key, used=0, cap=cap)
            .on_conflict_do_nothing(index_elements=["institution_programme_id", "ledger_key"])
        )
        row = db.execute(stmt).one()
    return row[0], row[1]


def _seat_holders(db: Session, ip_id: int, key: LedgerKey) -> list[int]:
    """Records currently holding a seat in the bucket, in the order they took it."""
    return list(
        db.scalars(
            text(
                "SELECT student_record_id FROM seat_ledger_entries "
                "WHERE institution_programme_id = :ip AND ledger_key = :key "
                "AND student_record_id IS NOT NULL "
                "GROUP BY student_record_id HAVING SUM(delta) > 0 "
                "ORDER BY MAX(id) FILTER (WHERE delta > 0)"
            ),
            {"ip": ip_id, "key": str(key)},
        )
    )


def _rederive_flags(db: Session, meta: IPMeta, key: LedgerKey, cap: int, used: int):
    """Re-apply the flag invariant for one bucket (caller holds the lock)."""
    holders = _seat_holders(db, meta.id, key)
    clean, flagged = holders[:cap], holders[cap:]
    if flagged:
        db.execute(
            update(StudentRecord)
            .where(StudentRecord.id.in_(flagged), StudentRecord.over_cap.is_(False))
            .values(over_cap=True)
        )
    if clean:
        db.execute(
            update(StudentRecord)
            .where(StudentRecord.id.in_(clean), StudentRecord.over_cap.is_(True))
            .values(over_cap=False)
        )
    return _sync_case(db, meta, key, cap, used, flagged_ids=flagged)


def case_key(ip_id: int, key: LedgerKey) -> str:
    return f"SEAT_OVER_CAP:{ip_id}:{key}"


def open_over_cap_case(db: Session, ip_id: int, key: LedgerKey) -> Case | None:
    return db.scalar(
        select(Case)
        .where(
            Case.type == CaseType.SEAT_OVER_CAP,
            Case.status == CaseStatus.OPEN,
            Case.subject_ids_json["key"].astext == case_key(ip_id, key),
        )
        .execution_options(populate_existing=True)
    )


def _sync_case(
    db: Session,
    meta: IPMeta,
    key: LedgerKey,
    cap: int,
    used: int,
    *,
    add: list[int] | None = None,
    flagged_ids: list[int] | None = None,
    closing_note: str = "Closed automatically: the bucket is back within its cap.",
) -> int | None:
    """Open, update or close the AICTE-only SEAT_OVER_CAP case of one bucket."""
    case = open_over_cap_case(db, meta.id, key)
    excess = max(0, used - cap)
    evidence = {
        "bucket": str(key),
        "cap": cap,
        "used": used,
        "excess": excess,
        "institution_programme_id": meta.id,
        "institution_code": meta.institution_code,
        "programme_code": meta.programme_code,
        "academic_year": meta.academic_year,
        "shift": meta.shift,
    }
    if excess <= 0:
        if case is not None:
            case.status = CaseStatus.RESOLVED
            case.decided_at = datetime.now(UTC)
            case.decision_note = case.decision_note or closing_note
            case.evidence_json = {**evidence, "flagged_record_ids": []}
            case.subject_ids_json = {**case.subject_ids_json, "record_ids": []}
            case.score = 0.0
            db.flush()
        return None
    if flagged_ids is None:
        if case is not None:
            flagged_ids = sorted(set(case.subject_ids_json.get("record_ids", [])) | set(add or []))
        else:
            flagged_ids = _seat_holders(db, meta.id, key)[cap:]
    evidence["flagged_record_ids"] = list(flagged_ids)
    subject = {
        "key": case_key(meta.id, key),
        "institution_programme_id": meta.id,
        "ledger_key": str(key),
        "record_ids": list(flagged_ids),
    }
    explanation = {
        "rule": "FR-10",
        "summary_en": (
            f"{meta.institution_code} {meta.programme_code} ({meta.academic_year}, "
            f"{meta.shift}): {used} admissions in the {key} bucket against a cap of {cap} "
            f"({excess} over). Records are stored, flagged and held for review."
        ),
    }
    if case is None:
        case = Case(
            type=CaseType.SEAT_OVER_CAP,
            visibility=CaseVisibility.AICTE_ONLY,
            priority=1 if excess >= 5 else 2,
            severity=Severity.WARNING,
            status=CaseStatus.OPEN,
            institution_id=meta.institution_id,
            subject_ids_json=subject,
            score=float(excess),
            evidence_json=evidence,
            explanation_json=explanation,
        )
        db.add(case)
    else:
        case.subject_ids_json = subject
        case.evidence_json = evidence
        case.explanation_json = {**(case.explanation_json or {}), **explanation}
        case.score = float(excess)
        case.priority = 1 if excess >= 5 else 2
    db.flush()
    return case.id


# --------------------------------------------------------------------------- admit / release


def admit(
    db: Session,
    record_id: int,
    *,
    batch_id: int | None = None,
    rules: dict[str, Any] | None = None,
) -> SeatResult:
    """Reserve one seat for a stored record (flag mode). Does not commit."""
    ip_id, category, entry_type = db.execute(
        select(
            StudentRecord.institution_programme_id,
            StudentRecord.admission_category,
            StudentRecord.entry_type,
        ).where(StudentRecord.id == record_id)
    ).one()
    key = bucket_for(category, entry_type)
    meta = ip_meta(db, ip_id)
    cap = cap_for(meta, key, rules if rules is not None else active_rules(db))

    used, stored_cap = _lock_balance(db, ip_id, key, cap)  # SELECT ... FOR UPDATE
    used += 1
    over = used > cap
    db.execute(
        insert(SeatLedgerEntry).values(
            institution_programme_id=ip_id,
            ledger_key=key,
            delta=1,
            student_record_id=record_id,
            batch_id=batch_id,
        )
    )
    db.execute(
        update(B)
        .where(B.institution_programme_id == ip_id, B.ledger_key == key)
        .values(used=used, cap=cap)
    )
    db.execute(
        update(StudentRecord)
        .where(StudentRecord.id == record_id)
        .values(over_cap=over, ledger_key=key)
    )
    case_id = None
    if stored_cap != cap:  # a rule changed since the last admission: re-derive the bucket
        case_id = _rederive_flags(db, meta, key, cap, used)
        over = bool(db.scalar(select(StudentRecord.over_cap).where(StudentRecord.id == record_id)))
    elif over:
        case_id = _sync_case(db, meta, key, cap, used, add=[record_id])
    return SeatResult(record_id, ip_id, key, cap, used, over, case_id, meta)


def admit_many(
    db: Session,
    meta: IPMeta,
    key: LedgerKey,
    record_ids: list[int],
    *,
    rules: dict[str, Any],
    batch_id: int | None = None,
) -> list[SeatResult]:
    """`admit()` for several records of one bucket, in the given order, under one lock.
    Equivalent to calling `admit()` for each record inside the same transaction."""
    if not record_ids:
        return []
    cap = cap_for(meta, key, rules)
    used, stored_cap = _lock_balance(db, meta.id, key, cap)
    results, over_ids, clean_ids = [], [], []
    for rid in record_ids:
        used += 1
        over = used > cap
        (over_ids if over else clean_ids).append(rid)
        results.append(SeatResult(rid, meta.id, key, cap, used, over, None, meta))
    db.execute(
        insert(SeatLedgerEntry),
        [
            dict(
                institution_programme_id=meta.id,
                ledger_key=key,
                delta=1,
                student_record_id=rid,
                batch_id=batch_id,
            )
            for rid in record_ids
        ],
    )
    db.execute(
        update(B)
        .where(B.institution_programme_id == meta.id, B.ledger_key == key)
        .values(used=used, cap=cap)
    )
    for ids, flag in ((over_ids, True), (clean_ids, False)):
        if ids:
            db.execute(
                update(StudentRecord)
                .where(StudentRecord.id.in_(ids))
                .values(over_cap=flag, ledger_key=key)
            )
    if stored_cap != cap:
        case_id = _rederive_flags(db, meta, key, cap, used)
    elif over_ids:
        case_id = _sync_case(db, meta, key, cap, used, add=over_ids)
    else:
        case_id = None
    for r in results:
        r.case_id = case_id if r.over else None
    return results


def release(
    db: Session,
    record_id: int,
    *,
    batch_id: int | None = None,
    rules: dict[str, Any] | None = None,
) -> SeatResult | None:
    """Give back a record's seat (rejection / correction). Idempotent: a record that holds
    no seat is left alone and None is returned. Does not commit."""
    ip_id, key = db.execute(
        select(StudentRecord.institution_programme_id, StudentRecord.ledger_key).where(
            StudentRecord.id == record_id
        )
    ).one()
    key = LedgerKey(key)
    meta = ip_meta(db, ip_id)
    cap = cap_for(meta, key, rules if rules is not None else active_rules(db))
    used, _ = _lock_balance(db, ip_id, key, cap)  # same lock as admit
    held = db.scalar(
        select(func.coalesce(func.sum(SeatLedgerEntry.delta), 0)).where(
            SeatLedgerEntry.student_record_id == record_id,
            SeatLedgerEntry.institution_programme_id == ip_id,
            SeatLedgerEntry.ledger_key == key,
        )
    )
    if held <= 0:
        return None
    used -= 1
    db.execute(
        insert(SeatLedgerEntry).values(
            institution_programme_id=ip_id,
            ledger_key=key,
            delta=-1,
            student_record_id=record_id,
            batch_id=batch_id,
        )
    )
    db.execute(
        update(B)
        .where(B.institution_programme_id == ip_id, B.ledger_key == key)
        .values(used=used, cap=cap)
    )
    db.execute(update(StudentRecord).where(StudentRecord.id == record_id).values(over_cap=False))
    case_id = _rederive_flags(db, meta, key, cap, used)
    return SeatResult(record_id, ip_id, key, cap, used, False, case_id, meta)


def release_many(
    db: Session,
    meta: IPMeta,
    key: LedgerKey,
    record_ids: list[int],
    *,
    rules: dict[str, Any],
    batch_id: int | None = None,
) -> list[int]:
    """`release()` for several records of one bucket under one lock (batch rejection).
    Same end state as releasing them one by one in the same transaction: the flags and the
    over-cap case are re-derived once at the end. Records holding no seat are skipped.
    Returns the ids whose seat was released. Does not commit."""
    if not record_ids:
        return []
    key = LedgerKey(key)
    cap = cap_for(meta, key, rules)
    used, _ = _lock_balance(db, meta.id, key, cap)  # same lock as admit
    held = [
        rid
        for rid, total in db.execute(
            select(SeatLedgerEntry.student_record_id, func.sum(SeatLedgerEntry.delta))
            .where(
                SeatLedgerEntry.student_record_id.in_(record_ids),
                SeatLedgerEntry.institution_programme_id == meta.id,
                SeatLedgerEntry.ledger_key == key,
            )
            .group_by(SeatLedgerEntry.student_record_id)
        )
        if total > 0
    ]
    if not held:
        return []
    used -= len(held)
    db.execute(
        insert(SeatLedgerEntry),
        [
            dict(
                institution_programme_id=meta.id,
                ledger_key=key,
                delta=-1,
                student_record_id=rid,
                batch_id=batch_id,
            )
            for rid in held
        ],
    )
    db.execute(
        update(B)
        .where(B.institution_programme_id == meta.id, B.ledger_key == key)
        .values(used=used, cap=cap)
    )
    db.execute(update(StudentRecord).where(StudentRecord.id.in_(held)).values(over_cap=False))
    _rederive_flags(db, meta, key, cap, used)
    return held


def recompute_caps(db: Session, rules: dict[str, Any] | None = None) -> dict[str, int]:
    """Apply the active quota rules to every balance (after a rule change). Buckets whose
    cap changed get their flags and case re-derived. Does not commit."""
    rules = rules if rules is not None else active_rules(db)
    metas = all_ip_meta(db)
    changed = 0
    rows = db.execute(select(B.institution_programme_id, B.ledger_key, B.cap)).all()
    for ip_id, key, cap in rows:
        meta = metas[ip_id]
        key = LedgerKey(key)
        new_cap = cap_for(meta, key, rules)
        if new_cap == cap:
            continue
        used, _ = _lock_balance(db, ip_id, key, new_cap)
        db.execute(
            update(B)
            .where(B.institution_programme_id == ip_id, B.ledger_key == key)
            .values(cap=new_cap)
        )
        _rederive_flags(db, meta, key, new_cap, used)
        changed += 1
    return {"balances": len(rows), "caps_changed": changed}


def ensure_balances(db: Session, rules: dict[str, Any] | None = None) -> int:
    """Create a zero balance (with its cap) for every institution-programme x bucket."""
    rules = rules if rules is not None else active_rules(db)
    values = [
        dict(institution_programme_id=m.id, ledger_key=k, used=0, cap=cap_for(m, k, rules))
        for m in all_ip_meta(db).values()
        for k in LedgerKey
    ]
    if values:
        db.execute(
            pg_insert(B)
            .values(values)
            .on_conflict_do_nothing(index_elements=["institution_programme_id", "ledger_key"])
        )
    return len(values)


# --------------------------------------------------------------------------- FR-11


ACCEPT_EXCEPTION = "ACCEPT_EXCEPTION"
REQUEST_CORRECTION = "REQUEST_CORRECTION"


def decide_over_cap_case(
    db: Session,
    case: Case,
    action: str,
    note: str,
    officer: User,
    record_ids: list[int] | None = None,
) -> dict[str, Any]:
    """AICTE officer decision on a SEAT_OVER_CAP case (FR-11). Does not commit.

    ACCEPT_EXCEPTION: the flags are marked reviewed (`over_cap_reviewed`) and the case is
    resolved with the note. REQUEST_CORRECTION: the chosen records (default: every flagged
    record of the case) move to NEEDS_CORRECTION, their seats are released (delta -1), and
    the institution's users are notified (MOCK notifications table)."""
    if case.type != CaseType.SEAT_OVER_CAP:
        raise ApiError(400, "CASE_TYPE_UNSUPPORTED", "Not a seat over-cap case.")
    # Lock order is always balance row first, then case (as in admit/release): take the
    # bucket lock, then re-read the case so a concurrent admission cannot interleave.
    ip_id = case.subject_ids_json["institution_programme_id"]
    key = LedgerKey(case.subject_ids_json["ledger_key"])
    rules = active_rules(db)
    _lock_balance(db, ip_id, key, cap_for(ip_meta(db, ip_id), key, rules))
    db.refresh(case)
    if case.status != CaseStatus.OPEN:
        raise ApiError(409, "CASE_ALREADY_DECIDED", "This case has already been decided.")
    flagged = list(case.subject_ids_json.get("record_ids", []))
    now = datetime.now(UTC)
    decision = {"action": action, "by": officer.id, "at": now.isoformat(), "note": note}
    history = list((case.explanation_json or {}).get("decisions", []))

    if action == ACCEPT_EXCEPTION:
        if flagged:
            db.execute(
                update(StudentRecord)
                .where(StudentRecord.id.in_(flagged))
                .values(over_cap_reviewed=True)
            )
        case.status = CaseStatus.RESOLVED
        case.decided_by, case.decided_at, case.decision_note = officer.id, now, note
        case.explanation_json = {**(case.explanation_json or {}), "decisions": [*history, decision]}
        db.flush()
        return {"action": action, "case_status": str(case.status), "records": flagged}

    if action != REQUEST_CORRECTION:
        raise ApiError(422, "DECISION_INVALID", "Unknown decision for a seat over-cap case.")
    chosen = list(record_ids or flagged)
    valid = set(
        db.scalars(
            select(StudentRecord.id).where(
                StudentRecord.id.in_(chosen),
                StudentRecord.institution_programme_id == ip_id,
                StudentRecord.ledger_key == key,
            )
        )
    )
    if not chosen or valid != set(chosen):
        raise ApiError(
            422,
            "DECISION_INVALID",
            "Correction can only be requested for records in this case's bucket.",
            details={"record_ids": sorted(set(chosen) - valid)},
        )
    released = []
    for rid in chosen:
        db.execute(
            update(StudentRecord)
            .where(StudentRecord.id == rid)
            .values(status=RecordStatus.NEEDS_CORRECTION)
        )
        if release(db, rid, rules=rules) is not None:
            released.append(rid)
    db.refresh(case)
    case.decided_by, case.decided_at, case.decision_note = officer.id, now, note
    case.explanation_json = {
        **(case.explanation_json or {}),
        "decisions": [*history, {**decision, "record_ids": chosen}],
    }
    meta = ip_meta(db, ip_id)
    _notify_institution(db, meta, key, chosen, note)
    db.flush()
    return {
        "action": action,
        "case_status": str(case.status),
        "records": chosen,
        "released": released,
    }


def _notify_institution(db: Session, meta: IPMeta, key: LedgerKey, ids: list[int], note: str):
    users = db.scalars(
        select(User.id).where(
            User.institution_id == meta.institution_id,
            User.role.in_([Role.INST_MAKER, Role.INST_CHECKER]),
            User.active.is_(True),
        )
    ).all()
    payload = {
        "message_key": "notification.seat_correction_requested",
        "message_en": (
            f"AICTE requested correction of {len(ids)} record(s) in the {key} bucket of "
            f"{meta.programme_code} ({meta.academic_year}). Their seats were released; please "
            "correct and resubmit them."
        ),
        "record_ids": ids,
        "bucket": str(key),
        "programme_code": meta.programme_code,
        "academic_year": meta.academic_year,
        "note": note,
        "channel": "MOCK",
    }
    for uid in users:
        db.add(Notification(user_id=uid, kind="SEAT_CORRECTION_REQUESTED", payload_json=payload))
