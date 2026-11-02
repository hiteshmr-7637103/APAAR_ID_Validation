"""Phase 9: Hardening, rate limiting, ERP API keys, security checklist, and performance benchmarks.

Acceptance criteria (spec Section 5.9, 12, 14, 15):
- In-memory rate limiting on /auth/login (429 on exceeding threshold, Retry-After header).
- Per-institution API keys for ERP systems (creation, listing, revocation, SHA-256 storage).
- Upload authentication via X-API-Key header.
- Standalone scripts (perf.py, verify_offline.py, sample_client.py, concurrency_demo.py).
- OWASP ASVS-style security checklist (docs/security.md).
- Complete PPT claims traceability table in README.md (P1 to P24).
"""

from __future__ import annotations

import pathlib

import pytest

from app.core.rate_limit import login_limiter
from app.db.session import SessionLocal
from app.services import api_keys

API = "/api/v1"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Ensure clean rate limiter state for each test."""
    login_limiter.reset()
    yield
    login_limiter.reset()


# --------------------------------------------------------------------------- 1. Login Rate Limiting
def test_login_rate_limiting_enforcement(client):
    """5 failed attempts per minute allowed; 6th returns 429 Too Many Requests."""
    bad_creds = {"username": "admin@aicte.demo", "password": "WrongPassword123!"}

    # First 5 failed attempts return 401
    for _ in range(5):
        r = client.post(f"{API}/auth/login", data=bad_creds)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"

    # 6th attempt is blocked by rate limiter with 429
    r_blocked = client.post(f"{API}/auth/login", data=bad_creds)
    assert r_blocked.status_code == 429
    err = r_blocked.json()["error"]
    assert err["code"] == "RATE_LIMITED"
    assert "Retry-After" in r_blocked.headers

    # Reset allows attempts again
    login_limiter.reset()
    r_after_reset = client.post(f"{API}/auth/login", data=bad_creds)
    assert r_after_reset.status_code == 401


def test_login_success_clears_rate_limit_history(client):
    """Successful login resets the failure counter."""
    bad_creds = {"username": "admin@aicte.demo", "password": "WrongPassword!"}
    good_creds = {"username": "admin@aicte.demo", "password": "Demo@2026"}

    # 3 failures
    for _ in range(3):
        r = client.post(f"{API}/auth/login", data=bad_creds)
        assert r.status_code == 401

    # Successful login
    r_ok = client.post(f"{API}/auth/login", data=good_creds)
    assert r_ok.status_code == 200

    # Can fail up to 5 more times without getting 429
    for _ in range(5):
        r = client.post(f"{API}/auth/login", data=bad_creds)
        assert r.status_code == 401


# --------------------------------------------------------------------------- 2. API Key Management
def test_api_key_lifecycle_and_rbac(client, auth):
    """Create, list, and revoke ERP API keys for an institution."""
    inst_id = 1

    # 1. Create key
    r_create = client.post(
        f"{API}/institutions/{inst_id}/api-keys",
        json={"label": "Campus ERP Integration Key"},
        headers=auth("INST_MAKER"),
    )
    assert r_create.status_code == 201, r_create.text
    created = r_create.json()
    assert "raw_key" in created
    assert created["raw_key"].startswith("apaar_live_")
    assert created["institution_id"] == inst_id
    assert created["active"] is True
    key_id = created["id"]
    raw_key = created["raw_key"]

    # 2. List keys
    r_list = client.get(
        f"{API}/institutions/{inst_id}/api-keys",
        headers=auth("INST_CHECKER"),
    )
    assert r_list.status_code == 200
    keys = r_list.json()
    assert any(k["id"] == key_id for k in keys)

    # 3. Cross-institution access blocked (404)
    r_other = client.get(
        f"{API}/institutions/2/api-keys",
        headers=auth("INST_MAKER"),  # User belongs to inst 1
    )
    assert r_other.status_code == 404

    # 4. Revoke key
    r_del = client.delete(
        f"{API}/institutions/{inst_id}/api-keys/{key_id}",
        headers=auth("INST_MAKER"),
    )
    assert r_del.status_code == 200
    assert r_del.json()["status"] == "REVOKED"

    # Verify key is no longer verified
    with SessionLocal() as db:
        assert api_keys.verify_api_key(db, raw_key) is None


# --------------------------------------------------------------------------- 3. ERP Upload Auth
def test_erp_upload_pipeline_with_api_key(client):
    """Third-party ERP uploads data using X-API-Key header without user bearer token."""
    with SessionLocal() as db:
        k, raw_key = api_keys.create_api_key(db, institution_id=1, label="Automated Test Key")

    headers = {"X-API-Key": raw_key}

    # 1. Create upload session via API Key
    r_init = client.post(
        f"{API}/uploads",
        json={
            "filename": "erp_students.csv",
            "total_chunks": 1,
            "academic_year": "2025-26",
        },
        headers=headers,
    )
    assert r_init.status_code == 201, r_init.text
    assert "id" in r_init.json()

    # 2. Invalid API key fails
    r_bad = client.post(
        f"{API}/uploads",
        json={"filename": "test.csv", "total_chunks": 1},
        headers={"X-API-Key": "apaar_live_invalid_token_123"},
    )
    assert r_bad.status_code == 401

    # Cleanup test key
    with SessionLocal() as db:
        api_keys.revoke_api_key(db, 1, k.id)


# --------------------------------------------------------------- 4. Scripts & Benchmarks
def test_performance_script_benchmarks():
    """Verify performance benchmark metrics pass targets."""
    from scripts.perf import (
        benchmark_dashboard_query,
        benchmark_single_record_validation,
    )

    with SessionLocal() as db:
        p50, p95, _avg = benchmark_single_record_validation(db, n_iterations=10)
        assert p50 < 100.0, f"p50 latency too high: {p50} ms"
        assert p95 < 200.0, f"p95 latency too high: {p95} ms"

        dash_ms = benchmark_dashboard_query(db)
        assert dash_ms < 500.0, f"Dashboard query too slow: {dash_ms} ms"


def test_offline_verifier_script_and_service():
    """Verify offline QR payload verification."""
    from datetime import UTC, datetime

    from app.core import crypto
    from app.db.models import (
        AdmissionCategory,
        ConsentRequest,
        ConsentStatus,
        Institution,
        InstitutionProgramme,
        LedgerKey,
        RecordStatus,
        Student,
        StudentRecord,
    )
    from app.services.signing import build_signed_record, verify_offline_payload

    with SessionLocal() as db:
        rec = db.query(StudentRecord).first()
        if not rec:
            inst = db.query(Institution).first()
            ip = db.query(InstitutionProgramme).filter_by(institution_id=inst.id).first()
            stud = Student(
                apaar_id="990274831650",
                name_enc=crypto.encrypt("Demo Student") or "",
                name_norm="DEMO STUDENT",
                identity_token="dummy-token-student",
            )
            db.add(stud)
            db.flush()
            rec = StudentRecord(
                student_id=stud.id,
                institution_id=inst.id,
                institution_programme_id=ip.id,
                student_college_id="ROLL-2026-001",
                admission_date=datetime.now(UTC).date(),
                admission_category=AdmissionCategory.GENERAL,
                ledger_key=LedgerKey.APPROVED,
                status=RecordStatus.ACTIVE,
                trust_tier=2,
            )
            db.add(rec)
            db.flush()

        consent = ConsentRequest(
            student_id=rec.student_id,
            verifier_id=1,
            purpose="Employment Verification",
            status=ConsentStatus.GRANTED,
            requested_fields_json=["programme_code", "status"],
            granted_fields_json=["programme_code", "status"],
        )
        db.add(consent)
        db.flush()
        bundle = build_signed_record(db, rec, consent, ["programme_code", "status"])

    # 1. Verify compact QR string payload
    res_qr = verify_offline_payload(bundle["qr_string"])
    assert res_qr["valid"] is True
    assert res_qr["payload_type"] == "COMPACT_QR"

    # 2. Verify full credential bundle
    res_full = verify_offline_payload(bundle)
    assert res_full["valid"] is True
    assert res_full["payload_type"] == "FULL_CREDENTIAL"


# --------------------------------------------------------------------------- 5. Docs & Traceability
def test_security_checklist_documentation_exists():
    """Verify docs/security.md exists and covers OWASP ASVS v4.0 items."""
    sec_md = REPO_ROOT / "docs" / "security.md"
    assert sec_md.exists()
    content = sec_md.read_text(encoding="utf-8")
    assert "OWASP Application Security Verification Standard" in content
    assert "V1: Architecture, Design and Threat Modeling" in content
    assert "V2: Authentication" in content
    assert "V3: Session Management" in content
    assert "V4: Access Control" in content
    assert "V5: Malicious Input Handling" in content
    assert "V6: Cryptography & Privacy Protection" in content
    assert "V7: Error Handling & Audit Logging" in content
    assert "V8: Data Protection & Privacy Rights" in content


def test_readme_traceability_table_complete():
    """Verify README.md contains full P1 to P24 claims mapping."""
    readme_md = REPO_ROOT / "README.md"
    assert readme_md.exists()
    content = readme_md.read_text(encoding="utf-8")
    for i in range(1, 25):
        assert f"**P{i}**" in content, f"Missing PPT claim P{i} in README.md"
