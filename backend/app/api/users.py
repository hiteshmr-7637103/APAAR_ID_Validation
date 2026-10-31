"""User administration (AICTE_ADMIN only). Read-only in Phase 1."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import UserOut
from app.core.rbac import require_roles
from app.db.models import Role, User
from app.db.session import get_db

router = APIRouter(tags=["admin"])


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_roles(Role.AICTE_ADMIN)), db: Session = Depends(get_db)):
    return db.scalars(select(User).options(selectinload(User.institution)).order_by(User.id)).all()
