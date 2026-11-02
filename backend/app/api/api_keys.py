"""API key management endpoints for external ERP integration (FR-39, spec Section 5.9)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.rbac import AICTE_ROLES, INSTITUTION_ROLES, require_roles
from app.db.models import Role, User
from app.db.session import get_db
from app.services import api_keys

router = APIRouter(prefix="/institutions/{institution_id}/api-keys", tags=["api-keys"])
_STAFF = require_roles(*AICTE_ROLES, *INSTITUTION_ROLES)


class ApiKeyCreate(BaseModel):
    label: str | None = Field(None, max_length=128, description="Description for this ERP key")


class ApiKeyOut(BaseModel):
    id: int
    institution_id: int
    label: str | None
    active: bool
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    raw_key: str = Field(
        ...,
        description="Plaintext secret token (starts with apaar_live_). Returned only once.",
    )


def _check_institution_access(user: User, institution_id: int) -> None:
    if user.role not in AICTE_ROLES and user.institution_id != institution_id:
        raise ApiError(404, "NOT_FOUND", "Institution not found.", "errors.not_found")


@router.get("", response_model=list[ApiKeyOut], summary="List API keys for an institution")
def list_keys(
    institution_id: int,
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> list[ApiKeyOut]:
    _check_institution_access(user, institution_id)
    keys = api_keys.list_api_keys(db, institution_id)
    return [
        ApiKeyOut(
            id=k.id,
            institution_id=k.institution_id,
            label=k.label,
            active=k.active,
            last_used_at=k.last_used_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.post("", response_model=ApiKeyCreated, status_code=201, summary="Create an ERP API key")
def create_key(
    institution_id: int,
    body: ApiKeyCreate = ApiKeyCreate(),
    user: User = Depends(require_roles(Role.INST_MAKER, Role.INST_CHECKER, Role.AICTE_ADMIN)),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    _check_institution_access(user, institution_id)
    k, raw_token = api_keys.create_api_key(db, institution_id, label=body.label)
    return ApiKeyCreated(
        id=k.id,
        institution_id=k.institution_id,
        label=k.label,
        active=k.active,
        last_used_at=k.last_used_at,
        created_at=k.created_at,
        raw_key=raw_token,
    )


@router.delete("/{key_id}", summary="Revoke an ERP API key")
def revoke_key(
    institution_id: int,
    key_id: int,
    user: User = Depends(require_roles(Role.INST_MAKER, Role.INST_CHECKER, Role.AICTE_ADMIN)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _check_institution_access(user, institution_id)
    ok = api_keys.revoke_api_key(db, institution_id, key_id)
    if not ok:
        raise ApiError(404, "API_KEY_NOT_FOUND", "API key not found or already revoked.")
    return {"status": "REVOKED", "key_id": key_id}
