"""API key management for external ERP systems (FR-39, spec Section 5.9).

Enables institutions to integrate student information systems (SIS) and ERPs directly
into the APAAR validation upload pipeline. Keys are hashed with SHA-256 at rest.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApiKey


def create_api_key(
    db: Session, institution_id: int, label: str | None = None
) -> tuple[ApiKey, str]:
    """Generate a high-entropy API key for an institution.

    Returns the persisted ApiKey model instance and the unhashed raw token string
    (returned only once).
    """
    raw_token = f"apaar_live_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    api_key = ApiKey(
        institution_id=institution_id,
        key_hash=key_hash,
        label=label or "ERP Integration Key",
        active=True,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key, raw_token


def verify_api_key(db: Session, raw_token: str) -> ApiKey | None:
    """Verify an incoming API key and touch its last_used_at timestamp."""
    if not raw_token or not raw_token.startswith("apaar_live_"):
        return None

    key_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.active.is_(True)))
    if key is None:
        return None

    key.last_used_at = datetime.now(UTC)
    db.commit()
    return key


def list_api_keys(db: Session, institution_id: int) -> list[ApiKey]:
    """List all registered API keys for an institution."""
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.institution_id == institution_id)
            .order_by(ApiKey.created_at.desc())
        ).all()
    )


def revoke_api_key(db: Session, institution_id: int, key_id: int) -> bool:
    """Revoke an active API key for an institution."""
    key = db.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.institution_id == institution_id,
            ApiKey.active.is_(True),
        )
    )
    if not key:
        return False
    key.active = False
    db.commit()
    return True
