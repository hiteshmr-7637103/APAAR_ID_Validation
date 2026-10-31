"""Student records (spec 8): create (form), update (versioned), read (scoped).

- `POST /students` creates one record from the Student Record Form (FR-1, FR-10). Rules run
  first; ERRORs must be fixed (422 with the outcome list). Otherwise the record is stored
  as PENDING_APPROVAL in its own one-row batch (source FORM) for checker approval, takes
  its seat (flag mode) and rises to T1/T2.
- `PUT /students/{apaar_id}/records/{id}` appends a version (FR-4); a material edit resets
  the tier to T1, re-runs the checks and moves the seat if the bucket changed.
- `GET /students/{apaar_id}` returns the caller's own institution's records of that
  person with their version history (AICTE roles: all records).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.validation import StudentRecordIn, resolve_institution, to_record_input
from app.core.crypto import decrypt
from app.core.errors import ApiError
from app.core.rbac import AICTE_ROLES, INSTITUTION_ROLES, require_roles, scope_to_institution
from app.db.models import (
    InstitutionProgramme,
    Role,
    Student,
    StudentRecord,
    StudentRecordVersion,
    TrustEvent,
    User,
)
from app.db.session import get_db
from app.services.records import create_record, update_record

router = APIRouter(tags=["students"])


class StudentRecordCreate(StudentRecordIn):
    justification: str | None = Field(
        None, max_length=2000, description="Justification stored with any WARNING"
    )


class StudentRecordUpdate(StudentRecordIn):
    reason: str | None = Field(None, max_length=255, description="Why the record changed")


def _record_out(rec: StudentRecord) -> dict[str, Any]:
    return {
        "id": rec.id,
        "status": str(rec.status),
        "trust_tier": rec.trust_tier,
        "ledger_key": str(rec.ledger_key),
        "over_cap": rec.over_cap,
        "institution_programme_id": rec.institution_programme_id,
        "batch_id": rec.batch_id,
        "current_version": rec.current_version,
    }


@router.post("/students", status_code=201, summary="Create one student record (flag mode)")
def create_student_record(
    body: StudentRecordCreate,
    user: User = Depends(require_roles(Role.INST_MAKER)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    inp = to_record_input(
        StudentRecordIn(**body.model_dump(exclude={"justification"})),
        resolve_institution(db, user, body.institution_id),
    )
    result = create_record(db, inp, user, body.justification)
    db.commit()
    rec = result.record
    return {
        "record": {
            **_record_out(rec),
            "academic_year": result.report.ip.academic_year,
            "programme_code": result.report.ip.programme_code,
        },
        "seat": result.seat.to_dict(),
        "outcomes": [o.to_dict() for o in result.outcomes],
        "message_en": (
            "Record saved and held for review: it exceeds its seat bucket and has been "
            "flagged for an AICTE officer. It also awaits registrar approval."
            if rec.over_cap
            else "Record saved. It awaits registrar approval."
        ),
    }


def _not_found() -> ApiError:
    return ApiError(404, "RECORD_NOT_FOUND", "Record not found.", "errors.record_not_found")


@router.put(
    "/students/{apaar_id}/records/{record_id}",
    summary="Update a record (new version; material edits reset the tier to T1)",
)
def update_student_record(
    apaar_id: str,
    record_id: int,
    body: StudentRecordUpdate,
    user: User = Depends(require_roles(Role.INST_MAKER)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = scope_to_institution(
        select(StudentRecord)
        .join(Student, Student.id == StudentRecord.student_id)
        .where(StudentRecord.id == record_id, Student.apaar_id == apaar_id)
        .with_for_update(of=StudentRecord),
        user,
        StudentRecord.institution_id,
    )
    record = db.scalar(stmt)
    if record is None:
        raise _not_found()
    if body.apaar_id is not None and body.apaar_id != apaar_id:
        raise ApiError(
            422,
            "APAAR_CHANGE_NOT_ALLOWED",
            "The APAAR ID is the person key and cannot be changed by an update. Submit a new "
            "record with the correct APAAR ID instead.",
            "errors.apaar_change_not_allowed",
        )
    data = body.model_dump(exclude={"reason"})
    data["apaar_id"] = apaar_id
    inp = to_record_input(
        StudentRecordIn(**data), resolve_institution(db, user, body.institution_id)
    )
    result = update_record(db, record, inp, user, body.reason)
    db.commit()
    db.refresh(record)
    result.pop("report", None)
    return {
        "record": _record_out(record),
        **result,
        "message_en": (
            "No change: the submitted values equal the current version."
            if result["noop"]
            else f"Saved as version {result['version']}."
        ),
    }


@router.get("/students/{apaar_id}", summary="A person's records (own institution only)")
def get_student(
    apaar_id: str,
    user: User = Depends(require_roles(*AICTE_ROLES, *INSTITUTION_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = scope_to_institution(
        select(StudentRecord)
        .join(Student, Student.id == StudentRecord.student_id)
        .where(Student.apaar_id == apaar_id)
        .options(
            selectinload(StudentRecord.institution_programme).selectinload(
                InstitutionProgramme.programme
            )
        )
        .order_by(StudentRecord.id),
        user,
        StudentRecord.institution_id,
    )
    records = db.scalars(stmt).all()
    if not records:
        raise _not_found()
    ids = [r.id for r in records]
    versions: dict[int, list[dict]] = {}
    for v in db.scalars(
        select(StudentRecordVersion)
        .where(StudentRecordVersion.student_record_id.in_(ids))
        .order_by(StudentRecordVersion.student_record_id, StudentRecordVersion.version)
    ):
        snap = dict(v.data_json or {})
        name = snap.pop("submitted_name_enc", None)
        snap["submitted_name"] = decrypt(name) if name else None
        versions.setdefault(v.student_record_id, []).append(
            {
                "version": v.version,
                "changed_by": v.changed_by,
                "changed_at": v.changed_at.isoformat(),
                "reason": v.reason,
                "data": snap,
            }
        )
    events: dict[int, list[dict]] = {}
    for e in db.scalars(
        select(TrustEvent)
        .where(TrustEvent.student_record_id.in_(ids))
        .order_by(TrustEvent.student_record_id, TrustEvent.id)
    ):
        events.setdefault(e.student_record_id, []).append(
            {
                "from_tier": e.from_tier,
                "to_tier": e.to_tier,
                "reason": e.reason,
                "actor": e.actor,
                "ts": e.ts.isoformat(),
            }
        )
    return {
        "apaar_id": apaar_id,
        "records": [
            {
                **_record_out(r),
                "programme_code": r.institution_programme.programme.canonical_code,
                "academic_year": r.institution_programme.academic_year,
                "admission_date": r.admission_date.isoformat(),
                "admission_category": str(r.admission_category),
                "entry_type": str(r.entry_type),
                "versions": versions.get(r.id, []),
                "trust_events": events.get(r.id, []),
            }
            for r in records
        ],
    }
