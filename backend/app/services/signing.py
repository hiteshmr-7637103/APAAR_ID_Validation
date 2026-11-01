"""Cryptographic signing and offline verification service (spec 5.7, FR-28 to FR-33, spec 12).

Features:
- Ed25519 asymmetric signing keypair management.
- Canonical JSON serialization (sorted keys, compact separators, UTF-8).
- Selective-disclosure signed records (fresh signed credential per consent).
- Compact offline-verifiable QR payload generation (FR-30).
- Revocation registry check (FR-31).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import qrcode
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import crypto
from app.db.models import (
    ConsentRequest,
    Revocation,
    SigningKey,
    StudentRecord,
)


def canonical_json(data: Any) -> bytes:
    """Canonical JSON: sorted keys, compact separators (no whitespace), UTF-8 (spec 12)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def get_or_create_signing_key(
    db: Session,
) -> tuple[SigningKey, ed25519.Ed25519PrivateKey]:
    """Retrieve the active Ed25519 signing key or create one at first start (spec 12)."""
    key_row = db.scalar(
        select(SigningKey).where(SigningKey.active.is_(True)).order_by(SigningKey.id.desc())
    )
    if key_row is not None:
        private_bytes = crypto.decrypt_bytes(base64.b64decode(key_row.private_key_enc))
        priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)
        return key_row, priv_key

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    priv_raw = priv_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    encrypted_priv = crypto.encrypt_bytes(priv_raw)
    key_id = f"key-{secrets.token_hex(8)}"

    key_row = SigningKey(
        key_id=key_id,
        public_key=pub_raw.hex(),
        private_key_enc=base64.b64encode(encrypted_priv).decode("ascii"),
        active=True,
    )
    db.add(key_row)
    db.commit()
    db.refresh(key_row)
    return key_row, priv_key


def sign_payload(private_key: ed25519.Ed25519PrivateKey, data: dict[str, Any]) -> tuple[str, bytes]:
    """Sign payload dictionary and return (hex signature, canonical bytes)."""
    canon = canonical_json(data)
    sig = private_key.sign(canon)
    return sig.hex(), canon


def verify_signature(public_key_hex: str, canonical_bytes: bytes, signature_hex: str) -> bool:
    """Verify Ed25519 signature over canonical bytes."""
    try:
        pub_bytes = bytes.fromhex(public_key_hex)
        sig_bytes = bytes.fromhex(signature_hex)
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(sig_bytes, canonical_bytes)
        return True
    except (InvalidSignature, ValueError):
        return False


def build_signed_record(
    db: Session,
    record: StudentRecord,
    consent: ConsentRequest,
    granted_fields: list[str],
) -> dict[str, Any]:
    """Build a fresh, selective-disclosure signed credential for granted fields (FR-28, FR-29)."""
    key_row, priv_key = get_or_create_signing_key(db)

    stud = record.student
    apaar_id = stud.apaar_id if stud else ""

    # Decrypt name if granted
    decrypted_name = None
    if stud and stud.name_enc:
        try:
            decrypted_name = crypto.decrypt(stud.name_enc)
        except Exception:
            decrypted_name = stud.name_norm if stud else "[Encrypted Name]"

    all_possible_fields: dict[str, Any] = {
        "apaar_id": apaar_id,
        "student_name": decrypted_name,
        "dob": stud.dob.isoformat() if stud and stud.dob else None,
        "gender": stud.gender.value if stud and stud.gender else None,
        "institution_code": record.institution.code if record.institution else None,
        "institution_name": record.institution.name if record.institution else None,
        "programme_code": (
            record.institution_programme.programme.canonical_code
            if record.institution_programme and record.institution_programme.programme
            else None
        ),
        "programme_name": (
            record.institution_programme.programme.name
            if record.institution_programme and record.institution_programme.programme
            else None
        ),
        "academic_year": (
            record.institution_programme.academic_year if record.institution_programme else None
        ),
        "admission_date": (record.admission_date.isoformat() if record.admission_date else None),
        "student_college_id": record.student_college_id,
        "status": record.status.value,
        "trust_tier": f"T{record.trust_tier}",
    }

    # Selective disclosure: only include granted fields
    disclosed_fields: dict[str, Any] = {}
    for f in granted_fields:
        if f in all_possible_fields and all_possible_fields[f] is not None:
            disclosed_fields[f] = all_possible_fields[f]

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=7)
    nonce = secrets.token_hex(16)

    credential_payload = {
        "key_id": key_row.key_id,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "nonce": nonce,
        "consent_id": consent.id,
        "purpose": consent.purpose,
        "apaar_id_suffix": apaar_id[-4:] if apaar_id else "",
        "disclosed_fields": disclosed_fields,
    }

    sig_hex, canon_bytes = sign_payload(priv_key, credential_payload)
    record_hash = hashlib.sha256(canon_bytes).hexdigest()

    # Compact QR payload for offline verification (FR-30)
    inst_code = record.institution.code if record.institution else ""
    prog_code = (
        record.institution_programme.programme.canonical_code
        if record.institution_programme and record.institution_programme.programme
        else ""
    )

    qr_body = {
        "kid": key_row.key_id,
        "apaar": apaar_id[-4:] if apaar_id else "",
        "inst": inst_code,
        "prog": prog_code,
        "status": record.status.value,
        "tier": record.trust_tier,
        "iat": now.strftime("%Y-%m-%d"),
        "exp": expires_at.strftime("%Y-%m-%d"),
        "nonce": nonce[:8],
        "hash": record_hash[:16],
    }
    qr_sig_hex, _ = sign_payload(priv_key, qr_body)
    qr_payload = {**qr_body, "sig": qr_sig_hex}

    return {
        "credential": credential_payload,
        "signature": sig_hex,
        "public_key": key_row.public_key,
        "record_hash": record_hash,
        "qr_payload": qr_payload,
        "qr_string": json.dumps(qr_payload, separators=(",", ":")),
    }


def generate_qr_png_bytes(content: str) -> bytes:
    """Generate PNG bytes of QR code (spec 8)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def verify_credential(db: Session, signed_data: dict[str, Any]) -> dict[str, Any]:
    """Verify signed record or compact QR payload: signature, expiry, revocation (FR-30)."""
    # 1. Compact QR payload format
    if "kid" in signed_data and "sig" in signed_data:
        key_id = signed_data["kid"]
        key_row = db.scalar(select(SigningKey).where(SigningKey.key_id == key_id))
        if not key_row:
            return {"outcome": "INVALID", "reason": f"Unknown signing key: {key_id}"}

        # Check expiration
        exp_str = signed_data.get("exp")
        if exp_str:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=UTC)
                if datetime.now(UTC) > exp_date + timedelta(days=1):
                    return {"outcome": "EXPIRED", "reason": "Credential has expired"}
            except ValueError:
                pass

        # Check revocation
        hash_prefix = signed_data.get("hash")
        if hash_prefix:
            rev = db.scalar(
                select(Revocation).where(Revocation.record_hash.startswith(hash_prefix))
            )
            if rev:
                return {
                    "outcome": "REVOKED",
                    "reason": rev.reason or "Revoked by student or authority",
                }

        # Signature verification over reconstructed compact canonical structure
        payload_copy = dict(signed_data)
        sig_hex = payload_copy.pop("sig", "")
        canon = canonical_json(payload_copy)
        if not verify_signature(key_row.public_key, canon, sig_hex):
            return {"outcome": "INVALID", "reason": "Cryptographic signature mismatch"}

        return {
            "outcome": "VALID",
            "key_id": key_id,
            "status": signed_data.get("status"),
            "trust_tier": signed_data.get("tier"),
            "apaar_suffix": signed_data.get("apaar"),
            "institution_code": signed_data.get("inst"),
            "programme_code": signed_data.get("prog"),
        }

    # 2. Full credential format
    credential = signed_data.get("credential")
    signature = signed_data.get("signature")
    if not credential or not signature:
        return {"outcome": "INVALID", "reason": "Missing credential or signature payload"}

    key_id = credential.get("key_id")
    key_row = db.scalar(select(SigningKey).where(SigningKey.key_id == key_id))
    if not key_row:
        return {"outcome": "INVALID", "reason": f"Unknown key ID: {key_id}"}

    canon_bytes = canonical_json(credential)
    if not verify_signature(key_row.public_key, canon_bytes, signature):
        return {"outcome": "INVALID", "reason": "Cryptographic signature mismatch (tampered data)"}

    # Expiration check
    expires_at = credential.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            if datetime.now(UTC) > exp_dt:
                return {"outcome": "EXPIRED", "reason": "Credential has expired"}
        except ValueError:
            pass

    # Revocation check
    rec_hash = hashlib.sha256(canon_bytes).hexdigest()
    rev = db.scalar(select(Revocation).where(Revocation.record_hash == rec_hash))
    if rev:
        return {
            "outcome": "REVOKED",
            "reason": rev.reason or "Credential was revoked by student",
        }

    return {
        "outcome": "VALID",
        "key_id": key_id,
        "disclosed_fields": credential.get("disclosed_fields", {}),
        "apaar_suffix": credential.get("apaar_id_suffix"),
        "purpose": credential.get("purpose"),
        "record_hash": rec_hash,
    }
