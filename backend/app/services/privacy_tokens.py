"""Privacy-preserving tokens (FR-37).

identity_token = HMAC-SHA256(pepper, normalise(name) | dob | apaar_id)
contact tokens  = HMAC-SHA256(pepper, kind | normalised value) for phone, email, address

Tokens support *exact* matching without exposing the underlying values to institution-facing
views. Honest limit (README): fuzzy matching still runs on plaintext inside the AICTE trust
boundary; this is not private set intersection.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import date

from app.core.config import settings
from app.services.names import normalise_name


def _token(message: str) -> str:
    return hmac.new(
        settings.token_pepper.encode(), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def identity_token(name: str, dob: date | None, apaar_id: str | None) -> str:
    dob_s = dob.isoformat() if dob else ""
    return _token(f"id|{normalise_name(name)}|{dob_s}|{apaar_id or ''}")


def normalise_phone(phone: str | None) -> str | None:
    """Keep the last 10 digits (drops +91 / leading 0); None if fewer than 10 digits."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else None


def normalise_email(email: str | None) -> str | None:
    return email.strip().lower() if email and email.strip() else None


def normalise_address(address: str | None) -> str | None:
    if not address:
        return None
    tokens = re.findall(r"[a-z0-9]+", address.lower())
    return " ".join(tokens) or None


def contact_token(phone: str | None) -> str | None:
    """Token of the phone number (the `student_records.contact_token` column)."""
    p = normalise_phone(phone)
    return _token(f"phone|{p}") if p else None


def email_token(email: str | None) -> str | None:
    e = normalise_email(email)
    return _token(f"email|{e}") if e else None


def address_token(address: str | None) -> str | None:
    a = normalise_address(address)
    return _token(f"address|{a}") if a else None


def apaar_token(apaar_id: str | None) -> str | None:
    """Token of an APAAR ID alone. Used as the dedupe key of AICTE-only cross-institution
    cases so case keys never carry the raw ID."""
    return _token(f"apaar|{apaar_id}") if apaar_id else None
