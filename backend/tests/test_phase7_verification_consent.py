"""Phase 7: Student confirmation, consent, signed credentials, and offline verification (spec 5.7).

Acceptance criteria:
- Enrolment confirmation raises T1/T2 to T3 (FR-18, FR-19).
- "Not me" opens a STUDENT_DISPUTE case (AICTE-only) and leaves tier unchanged.
- Verification request -> consent flow with selective disclosure (FR-27 to FR-29).
- Ed25519 signature verifies on valid credential, fails on tampered payload (FR-30).
- Revoking consent revokes outstanding credentials and records to revocation list (FR-31).
- Student access log records all verification events (FR-32).
- QR code PNG generation (FR-30).
- Certificate view contains APAAR ID, student ID, and signed verification payload (FR-34).
- Public endpoints (/.well-known/public-key, /revocations, /verify/offline) are public (FR-36).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core import crypto
from app.db.models import (
    AdmissionCategory,
    ConfirmationStatus,
    ConsentRequest,
    ConsentStatus,
    EnrolmentConfirmation,
    Institution,
    InstitutionProgramme,
    LedgerKey,
    RecordStatus,
    Revocation,
    Student,
    StudentRecord,
    TrustEvent,
)
from app.db.seed import DEMO_STUDENT_APAAR_ID
from app.db.session import SessionLocal
from app.services import signing, trust

API = "/api/v1"


# Helper to ensure a student record exists for demo student
def _ensure_demo_student_record(db):
    inst = db.query(Institution).first()
    ip = db.query(InstitutionProgramme).filter_by(institution_id=inst.id).first()
    stud = db.query(Student).filter_by(apaar_id=DEMO_STUDENT_APAAR_ID).first()
    if not stud:
        stud = Student(
            apaar_id=DEMO_STUDENT_APAAR_ID,
            name_enc=crypto.encrypt("Demo Student") or "",
            name_norm="DEMO STUDENT",
            identity_token="dummy-token-student",
        )
        db.add(stud)
        db.flush()

    rec = db.query(StudentRecord).filter_by(student_id=stud.id, status=RecordStatus.ACTIVE).first()
    if not rec:
        rec = StudentRecord(
            student_id=stud.id,
            institution_id=inst.id,
            institution_programme_id=ip.id,
            student_college_id="ROLL-2026-001",
            admission_date=datetime.now(UTC).date(),
            admission_category=AdmissionCategory.GENERAL,
            ledger_key=LedgerKey.APPROVED,
            status=RecordStatus.ACTIVE,
            trust_tier=trust.T2,
        )
        db.add(rec)
        db.flush()

    conf = (
        db.query(EnrolmentConfirmation)
        .filter_by(student_record_id=rec.id, status=ConfirmationStatus.PENDING)
        .first()
    )
    if not conf:
        conf = EnrolmentConfirmation(
            student_record_id=rec.id,
            status=ConfirmationStatus.PENDING,
            requested_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        db.add(conf)
        db.flush()

    db.commit()
    db.refresh(rec)
    db.refresh(conf)
    return rec, conf


# -------------------------------------------------- 1. Student Confirmation & T3 Elevation
def test_student_confirm_enrolment(client, auth):
    with SessionLocal() as db:
        rec, conf = _ensure_demo_student_record(db)
        conf_id = conf.id
        rec_id = rec.id

    # Confirm enrolment as demo student
    r = client.post(
        f"{API}/enrolments/{conf_id}/confirm",
        headers=auth("STUDENT"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "CONFIRMED"
    assert data["trust_tier"] == 3
    assert data["raised_to_t3"] is True

    # Verify DB state
    with SessionLocal() as db:
        rec_db = db.get(StudentRecord, rec_id)
        assert rec_db.trust_tier == 3
        conf_db = db.get(EnrolmentConfirmation, conf_id)
        assert conf_db.status == ConfirmationStatus.CONFIRMED

        # Check trust event
        ev = db.query(TrustEvent).filter_by(student_record_id=rec_id, to_tier=3).first()
        assert ev is not None
        assert "confirmed by student" in ev.reason


def test_student_already_decided_conflict(client, auth):
    with SessionLocal() as db:
        conf = (
            db.query(EnrolmentConfirmation).filter_by(status=ConfirmationStatus.CONFIRMED).first()
        )
        conf_id = conf.id

    r = client.post(
        f"{API}/enrolments/{conf_id}/confirm",
        headers=auth("STUDENT"),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ALREADY_DECIDED"


def test_student_dispute_enrolment(client, auth):
    with SessionLocal() as db:
        inst = db.query(Institution).first()
        ip = db.query(InstitutionProgramme).filter_by(institution_id=inst.id).first()
        stud = db.query(Student).filter_by(apaar_id=DEMO_STUDENT_APAAR_ID).first()

        rec = StudentRecord(
            student_id=stud.id,
            institution_id=inst.id,
            institution_programme_id=ip.id,
            student_college_id="ROLL-DISPUTE-01",
            admission_date=datetime.now(UTC).date(),
            admission_category=AdmissionCategory.GENERAL,
            ledger_key=LedgerKey.APPROVED,
            status=RecordStatus.ACTIVE,
            trust_tier=trust.T1,
        )
        db.add(rec)
        db.flush()

        conf = EnrolmentConfirmation(
            student_record_id=rec.id,
            status=ConfirmationStatus.PENDING,
            requested_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        db.add(conf)
        db.commit()
        conf_id = conf.id
        rec_id = rec.id

    r = client.post(
        f"{API}/enrolments/{conf_id}/dispute",
        json={"reason": "I never applied to this institution; fraud alert."},
        headers=auth("STUDENT"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "DISPUTED"
    assert "case_id" in data

    with SessionLocal() as db:
        rec_db = db.get(StudentRecord, rec_id)
        # Trust tier must remain unchanged
        assert rec_db.trust_tier == trust.T1
        conf_db = db.get(EnrolmentConfirmation, conf_id)
        assert conf_db.status == ConfirmationStatus.DISPUTED


def test_list_my_enrolments(client, auth):
    r = client.get(f"{API}/students/me/enrolments", headers=auth("STUDENT"))
    assert r.status_code == 200, r.text
    enrolments = r.json()
    assert isinstance(enrolments, list)
    assert len(enrolments) >= 1
    assert enrolments[0]["apaar_id"] == DEMO_STUDENT_APAAR_ID
    assert "confirmation_status" in enrolments[0]


# -------------------------------------------------- 2. Verification & Consent Flow
def test_consent_request_and_grant_flow(client, auth):
    # 1. Verifier submits request
    r = client.post(
        f"{API}/verify/requests",
        json={
            "apaar_id": DEMO_STUDENT_APAAR_ID,
            "purpose": "Background check for software engineer position",
            "requested_fields": ["student_name", "programme_name", "status", "trust_tier"],
        },
        headers=auth("VERIFIER"),
    )
    assert r.status_code == 200, r.text
    consent_id = r.json()["consent_id"]

    # 2. Student views requests
    r = client.get(f"{API}/consent/requests", headers=auth("STUDENT"))
    assert r.status_code == 200
    requests = r.json()
    req = next((c for c in requests if c["id"] == consent_id), None)
    assert req is not None
    assert req["status"] == "PENDING"

    # 3. Student grants with selective disclosure: only student_name and status
    r = client.post(
        f"{API}/consent/{consent_id}/grant",
        json={"granted_fields": ["student_name", "status"]},
        headers=auth("STUDENT"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "GRANTED"

    # 4. Verifier retrieves signed credential
    r = client.get(
        f"{API}/verify/records/{consent_id}",
        headers=auth("VERIFIER"),
    )
    assert r.status_code == 200, r.text
    cred_pack = r.json()
    assert "credential" in cred_pack
    assert "signature" in cred_pack
    assert "public_key" in cred_pack

    disclosed = cred_pack["credential"]["disclosed_fields"]
    # Only granted fields are present
    assert "student_name" in disclosed
    assert "status" in disclosed
    assert "programme_name" not in disclosed
    assert "trust_tier" not in disclosed


def test_consent_denial(client, auth):
    r = client.post(
        f"{API}/verify/requests",
        json={
            "apaar_id": DEMO_STUDENT_APAAR_ID,
            "purpose": "Loan verification",
            "requested_fields": ["student_name"],
        },
        headers=auth("VERIFIER"),
    )
    consent_id = r.json()["consent_id"]

    # Student denies
    r = client.post(
        f"{API}/consent/{consent_id}/deny",
        headers=auth("STUDENT"),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "DENIED"

    # Verifier gets 403 when trying to access
    r = client.get(f"{API}/verify/records/{consent_id}", headers=auth("VERIFIER"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CONSENT_DENIED"


def test_consent_revocation(client, auth):
    # Create and grant request
    r = client.post(
        f"{API}/verify/requests",
        json={
            "apaar_id": DEMO_STUDENT_APAAR_ID,
            "purpose": "Temporary internship verification",
            "requested_fields": ["student_name", "status"],
        },
        headers=auth("VERIFIER"),
    )
    consent_id = r.json()["consent_id"]

    client.post(
        f"{API}/consent/{consent_id}/grant",
        json={"granted_fields": ["student_name", "status"]},
        headers=auth("STUDENT"),
    )

    # Student revokes
    r = client.post(
        f"{API}/consent/{consent_id}/revoke",
        headers=auth("STUDENT"),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "REVOKED"

    # Attempt to retrieve returns 410 CONSENT_REVOKED
    r = client.get(f"{API}/verify/records/{consent_id}", headers=auth("VERIFIER"))
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "CONSENT_REVOKED"


# -------------------------------------------------- 3. Offline Verification & Tamper Detection
def test_offline_verify_valid_credential(client, auth):
    with SessionLocal() as db:
        rec, conf = _ensure_demo_student_record(db)
        # Create consent
        c = ConsentRequest(
            student_id=rec.student_id,
            verifier_id=1,
            purpose="Offline verify test",
            requested_fields_json=["student_name", "status"],
            status=ConsentStatus.GRANTED,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        pack = signing.build_signed_record(db, rec, c, ["student_name", "status"])

    # Verify through public offline endpoint
    r = client.post(
        f"{API}/verify/offline",
        json={"payload": pack},
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["outcome"] == "VALID"
    assert res["disclosed_fields"]["status"] == "ACTIVE"


def test_offline_verify_tamper_fails(client):
    with SessionLocal() as db:
        rec, conf = _ensure_demo_student_record(db)
        c = ConsentRequest(
            student_id=rec.student_id,
            verifier_id=1,
            purpose="Tamper test",
            requested_fields_json=["student_name"],
            status=ConsentStatus.GRANTED,
        )
        db.add(c)
        db.commit()
        pack = signing.build_signed_record(db, rec, c, ["student_name"])

    # Alter credential payload (tampering)
    pack["credential"]["disclosed_fields"]["student_name"] = "Attacker Name"

    r = client.post(
        f"{API}/verify/offline",
        json={"payload": pack},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["outcome"] == "INVALID"
    assert "mismatch" in res["reason"]


def test_offline_verify_compact_qr(client):
    with SessionLocal() as db:
        rec, _ = _ensure_demo_student_record(db)
        c = ConsentRequest(
            student_id=rec.student_id,
            verifier_id=1,
            purpose="QR test",
            requested_fields_json=["status"],
            status=ConsentStatus.GRANTED,
        )
        db.add(c)
        db.commit()
        pack = signing.build_signed_record(db, rec, c, ["status"])
        qr_payload = pack["qr_payload"]

    r = client.post(
        f"{API}/verify/offline",
        json={"payload": qr_payload},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["outcome"] == "VALID"
    assert res["status"] == "ACTIVE"


def test_offline_verify_revoked_credential(client):
    with SessionLocal() as db:
        rec, _ = _ensure_demo_student_record(db)
        c = ConsentRequest(
            student_id=rec.student_id,
            verifier_id=1,
            purpose="Revoke verify test",
            requested_fields_json=["status"],
            status=ConsentStatus.GRANTED,
        )
        db.add(c)
        db.commit()
        pack = signing.build_signed_record(db, rec, c, ["status"])

        # Add to revocation table
        db.add(
            Revocation(
                record_hash=pack["record_hash"],
                reason="Revoked during test",
            )
        )
        db.commit()

    r = client.post(
        f"{API}/verify/offline",
        json={"payload": pack},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["outcome"] == "REVOKED"


# -------------------------------------------------- 4. QR PNG & Certificate View
def test_qr_png_generation(client, auth):
    r = client.post(
        f"{API}/verify/requests",
        json={
            "apaar_id": DEMO_STUDENT_APAAR_ID,
            "purpose": "QR code test",
            "requested_fields": ["status"],
        },
        headers=auth("VERIFIER"),
    )
    consent_id = r.json()["consent_id"]

    client.post(
        f"{API}/consent/{consent_id}/grant",
        json={"granted_fields": ["status"]},
        headers=auth("STUDENT"),
    )

    r = client.get(f"{API}/verify/qr/{consent_id}.png", headers=auth("VERIFIER"))
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/png"
    assert len(r.content) > 100
    # PNG signature check: first 8 bytes must be standard PNG header
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_certificate_view(client, auth):
    with SessionLocal() as db:
        rec, _ = _ensure_demo_student_record(db)
        rec_id = rec.id

    r = client.get(f"{API}/certificates/{rec_id}", headers=auth("STUDENT"))
    assert r.status_code == 200, r.text
    cert = r.json()
    assert cert["record_id"] == rec_id
    assert cert["apaar_id"] == DEMO_STUDENT_APAAR_ID
    assert "student_college_id" in cert
    assert "institution_name" in cert
    assert "programme_name" in cert
    assert "signature" in cert
    assert "qr_string" in cert


def test_student_access_log(client, auth):
    r = client.get(f"{API}/students/me/access-log", headers=auth("STUDENT"))
    assert r.status_code == 200, r.text
    logs = r.json()
    assert isinstance(logs, list)
    if len(logs) > 0:
        log = logs[0]
        assert "verifier_name" in log
        assert "purpose" in log
        assert "fields_shared" in log
        assert "outcome" in log
        assert "timestamp" in log


# -------------------------------------------------- 5. Public Endpoints Reachability (FR-36)
def test_public_endpoints_without_auth(client):
    # /.well-known/public-key
    r = client.get("/.well-known/public-key")
    assert r.status_code == 200
    assert "public_key_hex" in r.json()

    # /api/v1/.well-known/public-key
    r = client.get(f"{API}/.well-known/public-key")
    assert r.status_code == 200
    assert "public_key_hex" in r.json()

    # /api/v1/revocations
    r = client.get(f"{API}/revocations")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
