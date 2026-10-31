"""Audit log (FR-35, spec 8): GET /audit, GET /audit/verify (AICTE admin and reviewer)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.rbac import AICTE_ROLES, require_roles
from app.db.models import AuditLog, User
from app.db.session import get_db

router = APIRouter(prefix="/audit", tags=["audit"])
_AICTE = require_roles(*AICTE_ROLES)


@router.get("", summary="Audit entries, newest first")
def list_audit(
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: int | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: User = Depends(_AICTE),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "entries": [audit.to_dict(r) for r in rows]}


@router.get("/verify", summary="Recompute the hash chain; report the first broken entry")
def verify_audit(_: User = Depends(_AICTE), db: Session = Depends(get_db)) -> dict[str, Any]:
    return audit.verify(db)
