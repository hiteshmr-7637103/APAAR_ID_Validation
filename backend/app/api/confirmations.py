"""Student Portal: confirmations, disputes, and access log (FR-18, FR-19, FR-32)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import ApiError
from app.core.events import event_bus
from app.core.rbac import AICTE_ROLES, require_roles
from app.db.models import (
    Case,
    CaseStatus,
    CaseType,
    CaseVisibility,
    ConfirmationStatus,
    ConsentRequest,
    EnrolmentConfirmation,
    RecordStatus,
    Role,
    Severity,
    Student,
    StudentRecord,
    User,
    VerificationEvent,
)
from app.db.session import get_db
from app.services import trust

router = APIRouter(tags=["confirmations"])
_STUDENT_OR_AICTE = require_roles(Role.STUDENT, *AICTE_ROLES)
_STUDENT_ONLY = require_roles(Role.STUDENT)


class DisputeIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


@router.get("/students/me/enrolments", summary="List student enrolments (FR-19)")
def list_my_enrolments(
    apaar_id: str | None = Query(None, description="For AICTE inspection only"),
    user: User = Depends(_STUDENT_OR_AICTE),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    target_apaar = user.student_apaar_id
    if user.role in AICTE_ROLES and apaar_id:
        target_apaar = apaar_id

    if not target_apaar:
        return []

    stud = db.scalar(select(Student).where(Student.apaar_id == target_apaar))
    if not stud:
        return []

    records = db.scalars(
        select(StudentRecord)
        .where(
            StudentRecord.student_id == stud.id,
            StudentRecord.status != RecordStatus.REJECTED,
        )
        .order_by(StudentRecord.admission_date.desc(), StudentRecord.id.desc())
    ).all()

    enrolments = []
    now = datetime.now(UTC)

    for rec in records:
        conf = db.scalar(
            select(EnrolmentConfirmation)
            .where(EnrolmentConfirmation.student_record_id == rec.id)
            .order_by(EnrolmentConfirmation.id.desc())
        )
        if conf is None:
            conf = EnrolmentConfirmation(
                student_record_id=rec.id,
                status=ConfirmationStatus.PENDING,
                requested_at=now,
                expires_at=now + timedelta(days=30),
            )
            db.add(conf)
            db.commit()
            db.refresh(conf)

        inst = rec.institution
        ip = rec.institution_programme
        prog = ip.programme if ip else None

        enrolments.append(
            {
                "confirmation_id": conf.id,
                "record_id": rec.id,
                "apaar_id": target_apaar,
                "institution_name": inst.name if inst else "Unknown Institution",
                "institution_code": inst.code if inst else "",
                "programme_name": prog.name if prog else "Unknown Programme",
                "programme_code": prog.canonical_code if prog else "",
                "academic_year": ip.academic_year if ip else "",
                "admission_date": rec.admission_date.isoformat() if rec.admission_date else None,
                "trust_tier": rec.trust_tier,
                "status": rec.status.value,
                "confirmation_status": conf.status.value,
                "requested_at": conf.requested_at.isoformat(),
                "expires_at": conf.expires_at.isoformat() if conf.expires_at else None,
                "decided_at": conf.decided_at.isoformat() if conf.decided_at else None,
            }
        )

    return enrolments


@router.post(
    "/enrolments/{confirmation_id}/confirm",
    summary="Confirm enrolment -> raises to T3 (FR-18, FR-19)",
)
def confirm_enrolment(
    confirmation_id: int,
    user: User = Depends(_STUDENT_ONLY),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conf = db.get(EnrolmentConfirmation, confirmation_id)
    if not conf:
        raise ApiError(404, "CONFIRMATION_NOT_FOUND", "Enrolment confirmation not found.")

    rec = db.get(StudentRecord, conf.student_record_id)
    if not rec or not rec.student or rec.student.apaar_id != user.student_apaar_id:
        raise ApiError(403, "FORBIDDEN", "This enrolment belongs to a different student.")

    if conf.status != ConfirmationStatus.PENDING:
        raise ApiError(
            409, "ALREADY_DECIDED", f"Confirmation has already been {conf.status.value}."
        )

    now = datetime.now(UTC)
    if conf.expires_at and now > conf.expires_at:
        conf.status = ConfirmationStatus.EXPIRED
        db.commit()
        raise ApiError(410, "CONFIRMATION_EXPIRED", "This confirmation request has expired.")

    # 1. Update confirmation status
    conf.status = ConfirmationStatus.CONFIRMED
    conf.decided_at = now

    # 2. Raise record trust tier to T3 (FR-18)
    raised = trust.raise_tier(db, rec, trust.T3, trust.REASON_T3, f"student:{user.id}")

    # 3. Log audit event
    audit.record(
        db,
        "ENROLMENT_CONFIRM",
        actor=user,
        entity_type="student_record",
        entity_id=rec.id,
        detail={"confirmation_id": conf.id, "from_tier": rec.trust_tier, "to_tier": trust.T3},
    )

    db.commit()
    db.refresh(conf)
    db.refresh(rec)

    event_bus.publish_threadsafe("trust_tier_updated", {"record_id": rec.id, "tier": 3})

    return {
        "status": "CONFIRMED",
        "confirmation_id": conf.id,
        "record_id": rec.id,
        "trust_tier": rec.trust_tier,
        "raised_to_t3": raised,
        "message_en": "Enrolment successfully confirmed. Trust tier elevated to T3.",
    }


@router.post(
    "/enrolments/{confirmation_id}/dispute",
    summary="Dispute enrolment ('Not me') -> opens case (FR-19)",
)
def dispute_enrolment(
    confirmation_id: int,
    body: DisputeIn,
    user: User = Depends(_STUDENT_ONLY),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conf = db.get(EnrolmentConfirmation, confirmation_id)
    if not conf:
        raise ApiError(404, "CONFIRMATION_NOT_FOUND", "Enrolment confirmation not found.")

    rec = db.get(StudentRecord, conf.student_record_id)
    if not rec or not rec.student or rec.student.apaar_id != user.student_apaar_id:
        raise ApiError(403, "FORBIDDEN", "This enrolment belongs to a different student.")

    if conf.status != ConfirmationStatus.PENDING:
        raise ApiError(
            409, "ALREADY_DECIDED", f"Confirmation has already been {conf.status.value}."
        )

    now = datetime.now(UTC)
    conf.status = ConfirmationStatus.DISPUTED
    conf.decided_at = now

    # Flag record and open a STUDENT_DISPUTE case for AICTE
    case = Case(
        type=CaseType.STUDENT_DISPUTE,
        visibility=CaseVisibility.AICTE_ONLY,
        priority=1,
        severity=Severity.ERROR,
        status=CaseStatus.OPEN,
        institution_id=rec.institution_id,
        subject_ids_json={
            "student_record_id": rec.id,
            "apaar_id": rec.student.apaar_id if rec.student else None,
            "confirmation_id": conf.id,
        },
        explanation_json={
            "summary_en": f"Student disputed enrolment ('Not me'): {body.reason}",
            "reason": body.reason,
            "record_id": rec.id,
            "institution_id": rec.institution_id,
        },
    )
    db.add(case)

    audit.record(
        db,
        "ENROLMENT_DISPUTE",
        actor=user,
        entity_type="student_record",
        entity_id=rec.id,
        detail={"confirmation_id": conf.id, "reason": body.reason},
    )

    db.commit()
    db.refresh(case)
    db.refresh(conf)

    event_bus.publish_threadsafe("case_created", {"case_id": case.id, "type": "STUDENT_DISPUTE"})

    return {
        "status": "DISPUTED",
        "confirmation_id": conf.id,
        "case_id": case.id,
        "message_en": "Enrolment dispute registered. Held for AICTE review investigation.",
    }


@router.get("/students/me/access-log", summary="Student visible verification access log (FR-32)")
def get_access_log(
    user: User = Depends(_STUDENT_OR_AICTE),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    target_apaar = user.student_apaar_id
    if not target_apaar:
        return []

    stud = db.scalar(select(Student).where(Student.apaar_id == target_apaar))
    if not stud:
        return []

    events = db.scalars(
        select(VerificationEvent)
        .where(VerificationEvent.student_id == stud.id)
        .order_by(VerificationEvent.created_at.desc())
    ).all()

    logs = []
    for ev in events:
        verifier = db.get(User, ev.verifier_id) if ev.verifier_id else None
        consent = db.get(ConsentRequest, ev.consent_id) if ev.consent_id else None

        logs.append(
            {
                "id": ev.id,
                "verifier_name": (
                    verifier.full_name or verifier.email if verifier else "External Verifier"
                ),
                "verifier_email": verifier.email if verifier else "-",
                "purpose": consent.purpose if consent else "Direct Verification",
                "fields_shared": ev.fields_json or [],
                "outcome": ev.outcome,
                "timestamp": ev.created_at.isoformat(),
            }
        )

    return logs
