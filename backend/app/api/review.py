"""Cases (spec 8): GET /cases, GET /cases/{id}, POST /cases/{id}/decision.

Visibility goes through `rbac.scope_cases`: institution users only ever receive their own
`INSTITUTION`-visible cases, never `AICTE_ONLY` ones. Phase 3 supports decisions on
SEAT_OVER_CAP cases (FR-11); the other decision types arrive with the review queue
(Phase 5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import ApiError
from app.core.rbac import AICTE_ROLES, INSTITUTION_ROLES, require_roles, scope_cases
from app.db.models import Case, CaseStatus, CaseType, User
from app.db.session import get_db
from app.services import seat_ledger

router = APIRouter(prefix="/cases", tags=["cases"])


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: CaseType
    visibility: str
    priority: int
    severity: str
    status: CaseStatus
    institution_id: int | None
    subject_ids_json: dict[str, Any]
    score: float | None
    evidence_json: dict[str, Any] | None
    explanation_json: dict[str, Any] | None
    decided_by: int | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


class Decision(BaseModel):
    action: Literal["ACCEPT_EXCEPTION", "REQUEST_CORRECTION"]
    note: str = Field(..., min_length=3, max_length=2000)
    record_ids: list[int] | None = Field(
        None, description="REQUEST_CORRECTION only; default: every flagged record"
    )


def _not_found() -> ApiError:
    return ApiError(404, "CASE_NOT_FOUND", "Case not found.", "errors.case_not_found")


@router.get("", response_model=list[CaseOut])
def list_cases(
    type: CaseType | None = None,
    status: CaseStatus | None = None,
    institution_id: int | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_roles(*AICTE_ROLES, *INSTITUTION_ROLES)),
    db: Session = Depends(get_db),
):
    stmt = scope_cases(select(Case), user)
    if type is not None:
        stmt = stmt.where(Case.type == type)
    if status is not None:
        stmt = stmt.where(Case.status == status)
    if institution_id is not None:
        stmt = stmt.where(Case.institution_id == institution_id)
    stmt = stmt.order_by(Case.priority, Case.id.desc()).limit(limit).offset(offset)
    return db.scalars(stmt).all()


@router.get("/{case_id}", response_model=CaseOut)
def get_case(
    case_id: int,
    user: User = Depends(require_roles(*AICTE_ROLES, *INSTITUTION_ROLES)),
    db: Session = Depends(get_db),
):
    case = db.scalar(scope_cases(select(Case).where(Case.id == case_id), user))
    if case is None:
        raise _not_found()
    return case


@router.post("/{case_id}/decision", summary="Human decision on a case (FR-11 for over-cap)")
def decide_case(
    case_id: int,
    body: Decision,
    user: User = Depends(require_roles(*AICTE_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    case = db.scalar(scope_cases(select(Case).where(Case.id == case_id), user))
    if case is None:
        raise _not_found()
    if case.type != CaseType.SEAT_OVER_CAP:
        raise ApiError(
            400,
            "CASE_TYPE_UNSUPPORTED",
            "Decisions on this case type arrive with the review queue (Phase 5).",
            "errors.case_type_unsupported",
        )
    if body.record_ids is not None and body.action != seat_ledger.REQUEST_CORRECTION:
        raise ApiError(422, "DECISION_INVALID", "record_ids applies to REQUEST_CORRECTION only.")
    result = seat_ledger.decide_over_cap_case(
        db, case, body.action, body.note, user, record_ids=body.record_ids
    )
    audit.record(
        db,
        "CASE_DECISION",
        actor=user,
        entity_type="case",
        entity_id=case.id,
        detail={
            "case_type": str(case.type),
            "action": body.action,
            "records": result.get("records", []),
            "released": result.get("released", []),
            "case_status": result.get("case_status"),
        },
    )
    db.commit()
    db.refresh(case)
    return {**result, "case": CaseOut.model_validate(case).model_dump(mode="json")}
