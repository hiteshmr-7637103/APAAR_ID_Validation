"""Storing and updating student records (FR-1, FR-4, FR-10, FR-17, FR-18).

Pipeline for every stored record (spec FR-17, shared by the single-record form, CSV
uploads and corrections):

    rules (caller) -> store PENDING_APPROVAL at T0 -> version row -> AICTE-only cases ->
    Seat Ledger reservation (flag mode, `seat_ledger.admit`) -> validation results ->
    trust T1 (no ERROR) / T2 (registry match) -> [ML triage: Phase 5] -> checker decision

ERRORs must be fixed before a record is stored (spec 5.2); over-cap is never an error: the
record is stored, flagged and held for review (FR-10).

Updates (`update_record`) append a version (append-only, FR-4). A material change (name,
DOB, programme, bucket) resets the tier to T1 and re-runs the checks; a changed programme or
bucket moves the seat (release, then admit) under the ledger lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import audit
from app.core.crypto import decrypt, encrypt
from app.core.errors import ApiError
from app.db.models import (
    BatchSource,
    BatchStatus,
    RecordStatus,
    SeatLedgerEntry,
    Severity,
    Student,
    StudentRecord,
    StudentRecordVersion,
    UploadBatch,
    User,
    ValidationResult,
)
from app.services import seat_ledger, trust
from app.services.cases import persist_case_specs
from app.services.names import name_keys
from app.services.privacy_tokens import address_token, contact_token, email_token, identity_token
from app.services.rule_engine import (
    DbRuleContext,
    Outcome,
    RecordInput,
    ValidationReport,
    validate_record,
)

MATERIAL_FIELDS = ("full_name", "dob", "programme", "ledger_key")
NO_SEAT_STATUSES = frozenset({RecordStatus.REJECTED, RecordStatus.NEEDS_CORRECTION})


@dataclass
class StoredRecord:
    record: StudentRecord
    report: ValidationReport
    seat: seat_ledger.SeatResult
    outcomes: list[Outcome]
    tiers: list[int] = field(default_factory=list)


# Kept for callers from Phase 3.
CreateResult = StoredRecord


def outcome_rows(
    outcomes: list[Outcome],
    record_id: int | None,
    batch_id=None,
    row=None,
    justification: str | None = None,
):
    return [
        dict(
            batch_id=batch_id,
            student_record_id=record_id,
            row_number=row,
            code=o.code,
            severity=o.severity,
            field=o.field,
            message_key=o.message_key,
            message_en=o.message_en,
            evidence_json=o.evidence,
            suggested_fix_json=o.suggested_fix,
            resolved=False,
            # WARNINGs need a justification (spec 5.2); it is stored with the warning.
            resolution_note=justification if o.severity == Severity.WARNING else None,
        )
        for o in outcomes
    ]


def errors_refusal(report: ValidationReport) -> ApiError:
    return ApiError(
        422,
        "RECORD_HAS_ERRORS",
        "The record has errors that must be fixed before it can be saved.",
        "errors.record_has_errors",
        {"outcomes": [o.to_dict() for o in report.outcomes]},
    )


def student_values(inp: RecordInput) -> dict[str, Any]:
    keys = name_keys(inp.full_name)
    return dict(
        apaar_id=inp.apaar_id,
        name_enc=encrypt(inp.full_name),
        name_norm=keys.normalised,
        name_phonetic=" ".join(keys.phonetic),
        dob=inp.dob,
        gender=inp.gender,
        identity_token=identity_token(inp.full_name, inp.dob, inp.apaar_id),
    )


def _student_id(db: Session, inp: RecordInput) -> int:
    """The person row for this APAAR ID (created on first submission). An existing row
    keeps the name/DOB first submitted (D21); this submission's name is kept, encrypted, in
    the record's version snapshot."""
    sid = db.scalar(
        pg_insert(Student)
        .values(**student_values(inp))
        .on_conflict_do_nothing(index_elements=["apaar_id"])
        .returning(Student.id)
    )
    return sid or db.scalar(select(Student.id).where(Student.apaar_id == inp.apaar_id))


def snapshot(inp: RecordInput, report: ValidationReport, source: str) -> dict[str, Any]:
    """Version snapshot (FR-4). PII stays encrypted: the submitted name is Fernet
    ciphertext; phone/email/address live encrypted on the record only."""
    ip = report.ip
    return {
        "source": source,
        "apaar_id": inp.apaar_id,
        "institution_code": ip.institution_code,
        "programme_code": ip.programme_code,
        "academic_year": ip.academic_year,
        "shift": ip.shift,
        "student_college_id": inp.student_college_id,
        "admission_date": inp.admission_date.isoformat() if inp.admission_date else None,
        "end_date": inp.end_date.isoformat() if inp.end_date else None,
        "admission_category": inp.admission_category,
        "entry_type": inp.entry_type,
        "mode": inp.mode,
        "ledger_key": str(report.ledger_key),
        "nationality": inp.nationality,
        "is_nri": inp.is_nri,
        "is_oci": inp.is_oci,
        "is_foreign_national": inp.is_foreign_national,
        "gender": inp.gender,
        "submitted_name_enc": encrypt(inp.full_name),
        "submitted_dob": inp.dob.isoformat() if inp.dob else None,
        "pii": "encrypted in student_records / students",
    }


def _pii_values(inp: RecordInput) -> dict[str, Any]:
    return dict(
        nri_sponsor_enc=encrypt(inp.nri_sponsor),
        oci_number_enc=encrypt(inp.oci_number),
        id_document_note_enc=encrypt(inp.id_document_note),
        phone_enc=encrypt(inp.phone),
        email_enc=encrypt(inp.email),
        address_enc=encrypt(inp.address),
        contact_token=contact_token(inp.phone),
        email_token=email_token(inp.email),
        address_token=address_token(inp.address),
    )


def record_values(
    inp: RecordInput, report: ValidationReport, student_id: int, batch_id: int
) -> dict[str, Any]:
    """Column values of a freshly submitted record: PENDING_APPROVAL at T0."""
    return dict(
        student_id=student_id,
        institution_id=inp.institution_id,
        institution_programme_id=report.ip.id,
        student_college_id=inp.student_college_id,
        admission_date=inp.admission_date,
        end_date=inp.end_date,
        admission_category=inp.admission_category,
        entry_type=inp.entry_type,
        mode=inp.mode,
        ledger_key=report.ledger_key,
        over_cap=False,
        over_cap_reviewed=False,
        nationality=inp.nationality,
        is_nri=inp.is_nri,
        is_oci=inp.is_oci,
        is_foreign_national=inp.is_foreign_national,
        status=RecordStatus.PENDING_APPROVAL,
        trust_tier=trust.T0,  # T0: submitted by the institution
        current_version=1,
        batch_id=batch_id,
        **_pii_values(inp),
    )


def version_values(
    record_id: int,
    inp: RecordInput,
    report: ValidationReport,
    actor: User,
    source: str,
    batch_id: int,
    row_number: int | None,
) -> dict[str, Any]:
    return dict(
        student_record_id=record_id,
        version=1,
        changed_by=actor.id,
        reason=f"created ({source.lower()})",
        data_json={**snapshot(inp, report, source), "batch_id": batch_id, "row": row_number},
    )


def insert_records_bulk(
    db: Session,
    items: list[tuple[RecordInput, ValidationReport, int]],
    actor: User,
    *,
    batch_id: int,
    source: str,
    chunk: int = 2000,
) -> list[int]:
    """`insert_record` for many validated rows (CSV submission) with set-based statements:
    person rows, records (ids returned in input order) and version 1 rows. Cases are the
    caller's job (they reference the new ids). Does not commit."""
    first: dict[str, RecordInput] = {}
    for inp, report, _ in items:
        if report.has_error:
            raise errors_refusal(report)
        first.setdefault(inp.apaar_id, inp)
    people = [student_values(inp) for inp in first.values()]
    for i in range(0, len(people), chunk):
        db.execute(
            pg_insert(Student)
            .values(people[i : i + chunk])
            .on_conflict_do_nothing(index_elements=["apaar_id"])
        )
    apaars = list(first)
    sid: dict[str, int] = {}
    for i in range(0, len(apaars), chunk):
        sid.update(
            db.execute(
                select(Student.apaar_id, Student.id).where(
                    Student.apaar_id.in_(apaars[i : i + chunk])
                )
            ).all()
        )
    rows = [record_values(inp, report, sid[inp.apaar_id], batch_id) for inp, report, _ in items]
    ids: list[int] = []
    stmt = insert(StudentRecord).returning(StudentRecord.id, sort_by_parameter_order=True)
    for i in range(0, len(rows), chunk):
        ids.extend(db.execute(stmt, rows[i : i + chunk]).scalars().all())
    versions = [
        version_values(rid, inp, report, actor, source, batch_id, row)
        for rid, (inp, report, row) in zip(ids, items, strict=True)
    ]
    for i in range(0, len(versions), chunk):
        db.execute(insert(StudentRecordVersion), versions[i : i + chunk])
    return ids


def insert_record(
    db: Session,
    inp: RecordInput,
    report: ValidationReport,
    actor: User,
    *,
    batch_id: int,
    source: str,
    row_number: int | None = None,
) -> StudentRecord:
    """Pipeline steps before the seat: store the record at T0 as PENDING_APPROVAL, write
    version 1 and open/merge its AICTE-only cases. Does not commit."""
    if report.has_error:
        raise errors_refusal(report)
    record = StudentRecord(**record_values(inp, report, _student_id(db, inp), batch_id))
    db.add(record)
    db.flush()
    db.add(
        StudentRecordVersion(
            **version_values(record.id, inp, report, actor, source, batch_id, row_number)
        )
    )
    persist_case_specs(db, report.cases, record.id)
    return record


def store_record(
    db: Session,
    inp: RecordInput,
    report: ValidationReport,
    actor: User,
    *,
    batch_id: int,
    source: str,
    rules: dict[str, Any],
    row_number: int | None = None,
    justification: str | None = None,
) -> StoredRecord:
    """Store one validated record (no ERRORs) and run the rest of the FR-17 pipeline up to
    PENDING_APPROVAL. Does not commit."""
    record = insert_record(
        db, inp, report, actor, batch_id=batch_id, source=source, row_number=row_number
    )
    seat = seat_ledger.admit(db, record.id, batch_id=batch_id, rules=rules)
    outcomes = list(report.outcomes)
    if seat.outcome() is not None:
        outcomes.append(seat.outcome())
    rows = outcome_rows(outcomes, record.id, batch_id, row_number, justification)
    if rows:
        db.execute(insert(ValidationResult), rows)
    tiers = trust.apply_earned(db, record, False, report.apaar_status)
    # FR-17 next step, ML triage (blocking + pairwise scoring), arrives in Phase 5 and will
    # run as a background task after commit; it never changes status or tier (NFR-8).
    db.flush()
    db.refresh(record)
    return StoredRecord(record, report, seat, outcomes, tiers)


def new_batch(
    db: Session,
    actor: User,
    institution_id: int,
    source: str,
    *,
    academic_year: str | None = None,
    status: BatchStatus = BatchStatus.PENDING_APPROVAL,
    row_count: int = 1,
) -> UploadBatch:
    now = datetime.now(UTC)
    batch = UploadBatch(
        institution_id=institution_id,
        maker_id=actor.id,
        status=status,
        source=source,
        academic_year=academic_year,
        row_count=row_count,
        error_count=0,
        warning_count=0,
        submitted_at=now if status == BatchStatus.PENDING_APPROVAL else None,
    )
    db.add(batch)
    db.flush()
    return batch


def create_record(
    db: Session, inp: RecordInput, actor: User, justification: str | None = None
) -> StoredRecord:
    """Single-record form (FR-1): validate, then store the record in its own one-row batch
    (source FORM) that waits for checker approval like an upload. Does not commit."""
    ctx = DbRuleContext(db)
    report = validate_record(inp, ctx)
    if report.has_error:
        raise errors_refusal(report)
    batch = new_batch(
        db, actor, inp.institution_id, BatchSource.FORM, academic_year=report.ip.academic_year
    )
    result = store_record(
        db,
        inp,
        report,
        actor,
        batch_id=batch.id,
        source=BatchSource.FORM,
        rules=ctx.rules,
        row_number=1,
        justification=justification,
    )
    batch.warning_count = int(any(o.severity == Severity.WARNING for o in result.outcomes))
    batch.submit_json = {"stored": 1, "record_ids": [result.record.id]}
    audit.record(
        db,
        "RECORD_CREATE",
        actor=actor,
        entity_type="student_record",
        entity_id=result.record.id,
        detail={
            "batch_id": batch.id,
            "source": "FORM",
            "institution_id": inp.institution_id,
            "ledger_key": str(result.record.ledger_key),
            "over_cap": result.record.over_cap,
            "trust_tier": result.record.trust_tier,
        },
    )
    return result


# --------------------------------------------------------------------------- updates


def _held_seats(db: Session, record_id: int) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(SeatLedgerEntry.delta), 0)).where(
                SeatLedgerEntry.student_record_id == record_id
            )
        )
    )


def current_values(db: Session, record: StudentRecord) -> dict[str, Any]:
    """Name/DOB as last submitted for this record (version snapshot), falling back to the
    person row (seeded records keep a non-PII snapshot, D25)."""
    snap = db.scalar(
        select(StudentRecordVersion.data_json).where(
            StudentRecordVersion.student_record_id == record.id,
            StudentRecordVersion.version == record.current_version,
        )
    )
    snap = snap or {}
    student = record.student
    name = decrypt(snap["submitted_name_enc"]) if snap.get("submitted_name_enc") else None
    dob = snap.get("submitted_dob")
    return {
        "full_name": name if name is not None else decrypt(student.name_enc),
        "dob": date.fromisoformat(dob) if dob else student.dob,
    }


def update_record(
    db: Session,
    record: StudentRecord,
    inp: RecordInput,
    actor: User,
    reason: str | None = None,
) -> dict[str, Any]:
    """Append a new version of a stored record (FR-4). The caller has locked the record
    row (SELECT ... FOR UPDATE). Does not commit."""
    if record.status in (RecordStatus.REJECTED,):
        raise ApiError(
            409,
            "RECORD_NOT_EDITABLE",
            "A rejected record cannot be edited; submit a new record instead.",
            "errors.record_not_editable",
        )
    old = current_values(db, record)
    old_ip, old_key, old_status = record.institution_programme_id, record.ledger_key, record.status
    inp.record_id = record.id
    ctx = DbRuleContext(db)
    report = validate_record(inp, ctx)
    if report.has_error:
        raise errors_refusal(report)

    changed: list[str] = []
    material: list[str] = []
    if (inp.full_name or "").strip() != (old["full_name"] or "").strip():
        material.append("full_name")
    if inp.dob != old["dob"]:
        material.append("dob")
    if report.ip.id != old_ip:
        material.append("programme")
    if report.ledger_key != old_key:
        material.append("ledger_key")

    simple = {
        "student_college_id": inp.student_college_id,
        "admission_date": inp.admission_date,
        "end_date": inp.end_date,
        "admission_category": inp.admission_category,
        "entry_type": inp.entry_type,
        "mode": inp.mode,
        "nationality": inp.nationality,
        "is_nri": inp.is_nri,
        "is_oci": inp.is_oci,
        "is_foreign_national": inp.is_foreign_national,
    }
    for name, value in simple.items():
        if str(getattr(record, name) or "") != str(value or ""):
            changed.append(name)
    pii = _pii_values(inp)
    for name in ("nri_sponsor", "oci_number", "id_document_note", "phone", "email", "address"):
        if (decrypt(getattr(record, f"{name}_enc")) or "") != (getattr(inp, name) or ""):
            changed.append(name)
    changed = sorted(set(changed) | set(material))
    if not changed:
        return {"changed": [], "material": [], "version": record.current_version, "noop": True}

    holds_seat = _held_seats(db, record.id) > 0
    seat_moves = holds_seat and ("programme" in material or "ledger_key" in material)
    released = None
    if seat_moves:  # release from the old bucket under its lock, before the record moves
        released = seat_ledger.release(db, record.id, rules=ctx.rules)

    record.institution_programme_id = report.ip.id
    record.ledger_key = report.ledger_key
    for name, value in simple.items():
        setattr(record, name, value)
    for name, value in pii.items():
        setattr(record, name, value)
    record.current_version += 1
    version = record.current_version

    # The person row follows the edit only when this is its only enrolment (D46).
    if {"full_name", "dob"} & set(material):
        others = db.scalar(
            select(func.count()).where(
                StudentRecord.student_id == record.student_id, StudentRecord.id != record.id
            )
        )
        if not others:
            keys = name_keys(inp.full_name)
            db.execute(
                update(Student)
                .where(Student.id == record.student_id)
                .values(
                    name_enc=encrypt(inp.full_name),
                    name_norm=keys.normalised,
                    name_phonetic=" ".join(keys.phonetic),
                    dob=inp.dob,
                    gender=inp.gender,
                    identity_token=identity_token(inp.full_name, inp.dob, inp.apaar_id),
                )
            )

    new_batch_id = None
    if old_status == RecordStatus.NEEDS_CORRECTION:
        # A corrected record re-enters the pipeline: new seat, back to PENDING_APPROVAL in a
        # one-row CORRECTION batch for the checker.
        batch = new_batch(
            db,
            actor,
            record.institution_id,
            BatchSource.CORRECTION,
            academic_year=report.ip.academic_year,
        )
        new_batch_id = batch.id
        record.status = RecordStatus.PENDING_APPROVAL
        record.batch_id = batch.id
        batch.submit_json = {"stored": 1, "record_ids": [record.id], "corrected_version": version}
    db.flush()

    db.add(
        StudentRecordVersion(
            student_record_id=record.id,
            version=version,
            changed_by=actor.id,
            reason=(reason or "updated")[:255],
            data_json={
                **snapshot(inp, report, "update"),
                "changed_fields": changed,
                "material_fields": material,
                "previous_version": version - 1,
            },
        )
    )
    persist_case_specs(db, report.cases, record.id)

    seat = None
    if seat_moves or old_status == RecordStatus.NEEDS_CORRECTION:
        seat = seat_ledger.admit(db, record.id, batch_id=record.batch_id, rules=ctx.rules)

    # Earlier outcomes are superseded by this version's checks.
    db.execute(
        update(ValidationResult)
        .where(
            ValidationResult.student_record_id == record.id,
            ValidationResult.resolved.is_(False),
        )
        .values(resolved=True, resolution_note=f"superseded by version {version}")
    )
    outcomes = list(report.outcomes)
    if seat is not None and seat.outcome() is not None:
        outcomes.append(seat.outcome())
    rows = outcome_rows(outcomes, record.id, record.batch_id, None, reason)
    if rows:
        db.execute(insert(ValidationResult), rows)

    actor_tag = f"user:{actor.id}"
    tier_before = record.trust_tier
    reset = bool(material) and trust.reset_for_material_edit(db, record, material, actor_tag)
    reached = trust.apply_earned(db, record, False, report.apaar_status)
    db.flush()
    db.refresh(record)
    audit.record(
        db,
        "RECORD_UPDATE",
        actor=actor,
        entity_type="student_record",
        entity_id=record.id,
        detail={
            "version": version,
            "changed_fields": changed,
            "material_fields": material,
            "tier_before": tier_before,
            "tier_after": record.trust_tier,
            "seat_moved": bool(seat_moves),
            "correction_batch_id": new_batch_id,
        },
    )
    return {
        "changed": changed,
        "material": material,
        "version": version,
        "noop": False,
        "tier_reset": reset,
        "tiers_reached": reached,
        "seat_released": released.to_dict() if released else None,
        "seat": seat.to_dict() if seat else None,
        "correction_batch_id": new_batch_id,
        "outcomes": [o.to_dict() for o in outcomes],
        "report": report,
    }
