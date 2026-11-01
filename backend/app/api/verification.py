"""Verification, consent management, and credentials (spec 5.7, FR-26 to FR-34, spec 8)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit, crypto
from app.core.errors import ApiError
from app.core.rbac import (
    AICTE_ROLES,
    INSTITUTION_ROLES,
    require_roles,
)
from app.db.models import (
    ConsentRequest,
    ConsentStatus,
    Notification,
    RecordStatus,
    Revocation,
    Role,
    Student,
    StudentRecord,
    User,
    VerificationEvent,
)
from app.db.session import get_db
from app.services import signing

router = APIRouter(tags=["verification"])

_VERIFIER_OR_AICTE = require_roles(Role.VERIFIER, *AICTE_ROLES)
_STUDENT_ONLY = require_roles(Role.STUDENT)
_STAFF_OR_STUDENT = require_roles(Role.STUDENT, Role.VERIFIER, *AICTE_ROLES, *INSTITUTION_ROLES)


class VerifyRequestIn(BaseModel):
    apaar_id: str = Field(..., pattern=r"^[0-9]{12}$")
    purpose: str = Field(..., min_length=3, max_length=500)
    requested_fields: list[str] = Field(..., min_length=1)


class GrantConsentIn(BaseModel):
    granted_fields: list[str] = Field(..., min_length=1)


class OfflineVerifyIn(BaseModel):
    payload: dict[str, Any]


# --------------------------------------------------------------------------- 1. Consent Requests
@router.post("/verify/requests", summary="Submit a verification request (FR-26)")
def submit_verification_request(
    body: VerifyRequestIn,
    user: User = Depends(_VERIFIER_OR_AICTE),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    student = db.scalar(select(Student).where(Student.apaar_id == body.apaar_id))
    if not student:
        raise ApiError(
            404,
            "STUDENT_NOT_FOUND",
            f"No student record found with APAAR ID {body.apaar_id}.",
        )

    now = datetime.now(UTC)
    expires = now + timedelta(days=7)

    consent = ConsentRequest(
        student_id=student.id,
        verifier_id=user.id,
        purpose=body.purpose,
        requested_fields_json=body.requested_fields,
        status=ConsentStatus.PENDING,
        expires_at=expires,
    )
    db.add(consent)

    # Outbox notification for student (FR-27)
    db.add(
        Notification(
            student_id=student.id,
            kind="CONSENT_REQUEST",
            payload_json={
                "verifier_name": user.full_name or user.email,
                "purpose": body.purpose,
                "requested_fields": body.requested_fields,
                "expires_at": expires.isoformat(),
            },
        )
    )

    audit.record(
        db,
        "VERIFICATION_REQUEST",
        actor=user,
        entity_type="consent_request",
        entity_id=None,
        detail={"apaar_id": body.apaar_id, "purpose": body.purpose},
    )

    db.commit()
    db.refresh(consent)

    return {
        "consent_id": consent.id,
        "status": consent.status.value,
        "purpose": consent.purpose,
        "requested_fields": consent.requested_fields_json,
        "expires_at": consent.expires_at.isoformat() if consent.expires_at else None,
        "message_en": "Verification request submitted. Waiting for student consent.",
    }


@router.get("/consent/requests", summary="List consent requests for student or verifier (FR-27)")
def list_consent_requests(
    user: User = Depends(_STAFF_OR_STUDENT),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(ConsentRequest).order_by(ConsentRequest.created_at.desc())
    if user.role == Role.STUDENT:
        student = db.scalar(select(Student).where(Student.apaar_id == user.student_apaar_id))
        if not student:
            return []
        stmt = stmt.where(ConsentRequest.student_id == student.id)
    elif user.role == Role.VERIFIER:
        stmt = stmt.where(ConsentRequest.verifier_id == user.id)

    requests = db.scalars(stmt).all()
    results = []
    for c in requests:
        verifier = db.get(User, c.verifier_id)
        student = db.get(Student, c.student_id)
        results.append(
            {
                "id": c.id,
                "student_apaar_id": student.apaar_id if student else None,
                "verifier_name": verifier.full_name or verifier.email if verifier else "Verifier",
                "verifier_email": verifier.email if verifier else "-",
                "purpose": c.purpose,
                "requested_fields": c.requested_fields_json,
                "granted_fields": c.granted_fields_json,
                "status": c.status.value,
                "created_at": c.created_at.isoformat(),
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                "decided_at": c.decided_at.isoformat() if c.decided_at else None,
            }
        )
    return results


@router.post(
    "/consent/{consent_id}/grant",
    summary="Student grants consent with selective disclosure (FR-27, FR-29)",
)
def grant_consent(
    consent_id: int,
    body: GrantConsentIn,
    user: User = Depends(_STUDENT_ONLY),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    consent = db.get(ConsentRequest, consent_id)
    if not consent:
        raise ApiError(404, "CONSENT_NOT_FOUND", "Consent request not found.")

    student = db.get(Student, consent.student_id)
    if not student or student.apaar_id != user.student_apaar_id:
        raise ApiError(403, "FORBIDDEN", "This consent request belongs to another student.")

    if consent.status != ConsentStatus.PENDING:
        raise ApiError(
            409, "ALREADY_DECIDED", f"Consent request has already been {consent.status.value}."
        )

    now = datetime.now(UTC)
    if consent.expires_at and now > consent.expires_at:
        consent.status = ConsentStatus.EXPIRED
        db.commit()
        raise ApiError(410, "CONSENT_EXPIRED", "This consent request has expired.")

    # Fetch latest active student record to sign
    record = db.scalar(
        select(StudentRecord)
        .where(
            StudentRecord.student_id == student.id,
            StudentRecord.status != RecordStatus.REJECTED,
        )
        .order_by(StudentRecord.trust_tier.desc(), StudentRecord.id.desc())
    )
    if not record:
        raise ApiError(404, "RECORD_NOT_FOUND", "No active student record found to disclose.")

    consent.status = ConsentStatus.GRANTED
    consent.granted_fields_json = body.granted_fields
    consent.decided_at = now

    # Generate selective-disclosure signed record (FR-28, FR-29)
    signed_pack = signing.build_signed_record(db, record, consent, body.granted_fields)

    event = VerificationEvent(
        consent_id=consent.id,
        verifier_id=consent.verifier_id,
        student_id=student.id,
        fields_json=body.granted_fields,
        outcome="GRANTED",
        signed_record_json=signed_pack,
    )
    db.add(event)

    audit.record(
        db,
        "CONSENT_GRANT",
        actor=user,
        entity_type="consent_request",
        entity_id=consent.id,
        detail={"granted_fields": body.granted_fields, "record_hash": signed_pack["record_hash"]},
    )

    db.commit()

    return {
        "status": "GRANTED",
        "consent_id": consent.id,
        "granted_fields": body.granted_fields,
        "message_en": "Consent granted. Signed credential generated.",
    }


@router.post("/consent/{consent_id}/deny", summary="Student denies consent (FR-27)")
def deny_consent(
    consent_id: int,
    user: User = Depends(_STUDENT_ONLY),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    consent = db.get(ConsentRequest, consent_id)
    if not consent:
        raise ApiError(404, "CONSENT_NOT_FOUND", "Consent request not found.")

    student = db.get(Student, consent.student_id)
    if not student or student.apaar_id != user.student_apaar_id:
        raise ApiError(403, "FORBIDDEN", "This consent request belongs to another student.")

    now = datetime.now(UTC)
    consent.status = ConsentStatus.DENIED
    consent.decided_at = now

    db.add(
        VerificationEvent(
            consent_id=consent.id,
            verifier_id=consent.verifier_id,
            student_id=student.id,
            fields_json=[],
            outcome="DENIED",
        )
    )

    audit.record(
        db,
        "CONSENT_DENY",
        actor=user,
        entity_type="consent_request",
        entity_id=consent.id,
        detail={},
    )

    db.commit()
    return {"status": "DENIED", "consent_id": consent.id}


@router.post("/consent/{consent_id}/revoke", summary="Student revokes prior consent (FR-27, FR-31)")
def revoke_consent(
    consent_id: int,
    user: User = Depends(_STUDENT_ONLY),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    consent = db.get(ConsentRequest, consent_id)
    if not consent:
        raise ApiError(404, "CONSENT_NOT_FOUND", "Consent request not found.")

    student = db.get(Student, consent.student_id)
    if not student or student.apaar_id != user.student_apaar_id:
        raise ApiError(403, "FORBIDDEN", "This consent request belongs to another student.")

    if consent.status != ConsentStatus.GRANTED:
        raise ApiError(400, "NOT_GRANTED", "Only GRANTED consents can be revoked.")

    consent.status = ConsentStatus.REVOKED

    # Find the verification event to get the signed record hash to revoke
    event = db.scalar(
        select(VerificationEvent)
        .where(VerificationEvent.consent_id == consent.id, VerificationEvent.outcome == "GRANTED")
        .order_by(VerificationEvent.id.desc())
    )
    if event and event.signed_record_json:
        rec_hash = event.signed_record_json.get("record_hash")
        if rec_hash:
            rev = Revocation(record_hash=rec_hash, reason="Consent revoked by student")
            db.add(rev)

    db.add(
        VerificationEvent(
            consent_id=consent.id,
            verifier_id=consent.verifier_id,
            student_id=student.id,
            outcome="REVOKED",
        )
    )

    audit.record(
        db,
        "CONSENT_REVOKE",
        actor=user,
        entity_type="consent_request",
        entity_id=consent.id,
        detail={},
    )

    db.commit()
    return {
        "status": "REVOKED",
        "consent_id": consent.id,
        "message_en": "Consent and signed credentials revoked.",
    }


# --------------------------------------------- 2. Verifier Access to Signed Record & QR
@router.get(
    "/verify/records/{consent_id}",
    summary="Retrieve signed record with student consent (FR-28, FR-29)",
)
def get_verified_record(
    consent_id: int,
    user: User = Depends(_STAFF_OR_STUDENT),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    consent = db.get(ConsentRequest, consent_id)
    if not consent:
        raise ApiError(404, "CONSENT_NOT_FOUND", "Consent request not found.")

    if consent.status == ConsentStatus.PENDING:
        raise ApiError(
            403, "CONSENT_PENDING", "Student has not yet granted consent for this request."
        )
    if consent.status == ConsentStatus.DENIED:
        raise ApiError(403, "CONSENT_DENIED", "Student denied consent for this request.")
    if consent.status == ConsentStatus.REVOKED:
        raise ApiError(410, "CONSENT_REVOKED", "Student has revoked consent for this credential.")
    if consent.expires_at and datetime.now(UTC) > consent.expires_at:
        raise ApiError(410, "CONSENT_EXPIRED", "Consent has expired.")

    event = db.scalar(
        select(VerificationEvent)
        .where(VerificationEvent.consent_id == consent.id, VerificationEvent.outcome == "GRANTED")
        .order_by(VerificationEvent.id.desc())
    )
    if not event or not event.signed_record_json:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "No signed credential found.")

    # Record verification check
    db.add(
        VerificationEvent(
            consent_id=consent.id,
            verifier_id=user.id,
            student_id=consent.student_id,
            outcome="VERIFIED",
        )
    )
    db.commit()

    return event.signed_record_json


@router.get("/verify/qr/{consent_id}.png", summary="Download verification QR code PNG (FR-30)")
def get_verification_qr_png(
    consent_id: int,
    user: User = Depends(_STAFF_OR_STUDENT),
    db: Session = Depends(get_db),
) -> Response:
    signed_data = get_verified_record(consent_id, user=user, db=db)
    qr_string = signed_data.get("qr_string", "")
    png_bytes = signing.generate_qr_png_bytes(qr_string)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="qr_{consent_id}.png"'},
    )


# --------------------------------------------- 3. Public Verification & Offline API
@router.post("/verify/offline", summary="Offline verification checker (FR-30, FR-33)")
def verify_offline_payload(
    body: OfflineVerifyIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Public verification endpoint: checks signature, validity, expiration, and revocations."""
    return signing.verify_credential(db, body.payload)


@router.get("/.well-known/public-key", summary="Public Ed25519 verification key (FR-30, FR-36)")
def get_public_key(db: Session = Depends(get_db)) -> dict[str, Any]:
    key_row, _ = signing.get_or_create_signing_key(db)
    return {
        "key_id": key_row.key_id,
        "algorithm": "Ed25519",
        "public_key_hex": key_row.public_key,
        "created_at": key_row.created_at.isoformat(),
    }


@router.get("/revocations", summary="Public revocation list (FR-31, FR-36)")
def get_revocations(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    revs = db.scalars(select(Revocation).order_by(Revocation.revoked_at.desc())).all()
    return [
        {
            "record_hash": r.record_hash,
            "reason": r.reason,
            "revoked_at": r.revoked_at.isoformat(),
        }
        for r in revs
    ]


# --------------------------------------------------------------------------- 4. Certificate View
@router.get(
    "/certificates/{record_id}", summary="Printable certificate view with signed QR (FR-34)"
)
def get_certificate_data(
    record_id: int,
    user: User = Depends(_STAFF_OR_STUDENT),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rec = db.get(StudentRecord, record_id)
    if not rec:
        raise ApiError(404, "RECORD_NOT_FOUND", "Student record not found.")

    inst = rec.institution
    ip = rec.institution_programme
    prog = ip.programme if ip else None

    stud = rec.student
    apaar_id = stud.apaar_id if stud else ""

    # Student name decryption
    name = "Student"
    if stud and stud.name_enc:
        try:
            name = crypto.decrypt(stud.name_enc) or "Student"
        except Exception:
            name = stud.name_norm if stud else "Student"

    key_row, priv_key = signing.get_or_create_signing_key(db)
    now = datetime.now(UTC)

    cert_payload = {
        "record_id": rec.id,
        "apaar_id": apaar_id,
        "student_college_id": rec.student_college_id,
        "student_name": name,
        "institution_code": inst.code if inst else "",
        "institution_name": inst.name if inst else "",
        "programme_name": prog.name if prog else "",
        "programme_code": prog.canonical_code if prog else "",
        "trust_tier": rec.trust_tier,
        "admission_date": rec.admission_date.isoformat() if rec.admission_date else "",
        "issued_at": now.strftime("%Y-%m-%d"),
        "key_id": key_row.key_id,
    }
    sig_hex, _ = signing.sign_payload(priv_key, cert_payload)

    qr_payload = {
        "kid": key_row.key_id,
        "apaar": apaar_id[-4:] if apaar_id else "",
        "inst": inst.code if inst else "",
        "prog": prog.canonical_code if prog else "",
        "status": rec.status.value,
        "tier": rec.trust_tier,
        "iat": now.strftime("%Y-%m-%d"),
        "sig": sig_hex,
    }

    return {
        **cert_payload,
        "signature": sig_hex,
        "public_key": key_row.public_key,
        "qr_payload": qr_payload,
        "qr_string": json.dumps(qr_payload, separators=(",", ":")),
    }
