"""Field-level encryption at rest (FR-38): Fernet for phone, email, address, sponsor,
OCI number and the student's name.

Key: `FERNET_KEY` from the environment. If it is empty (local demo), a DEV-ONLY key is
derived from `JWT_SECRET` so that `docker compose up` works without setup. That fallback is
not acceptable outside a local demo; production needs a managed key (KMS/HSM) and rotation.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.fernet_key.strip()
    if not key:
        digest = hashlib.sha256(b"apaar-ledger-dev-only-fernet:" + settings.jwt_secret.encode())
        key = base64.urlsafe_b64encode(digest.digest()).decode()
    return Fernet(key.encode())


def encrypt(value: str | None) -> str | None:
    """Encrypt a string; None and "" stay None (nothing to protect)."""
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str | None) -> str | None:
    if token is None:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:  # wrong key or tampered ciphertext
        raise ValueError("Could not decrypt field (wrong FERNET_KEY or tampered value)") from exc


def encrypt_bytes(value: bytes) -> bytes:
    """Fernet-encrypt raw bytes (upload chunks and assembled CSVs hold PII, FR-38)."""
    return _fernet().encrypt(value)


def decrypt_bytes(token: bytes) -> bytes:
    try:
        return _fernet().decrypt(bytes(token))
    except InvalidToken as exc:
        raise ValueError("Could not decrypt payload (wrong FERNET_KEY or tampered value)") from exc
