"""Cases and Review Queue API (spec 5.5, FR-17b; spec 8).

Endpoints:
- `GET /cases`: list cases with filters (type, status, priority, institution_id).
  Institution users only ever receive their own `INSTITUTION`-visible cases;
  `AICTE_ONLY` cases are never returned to institution callers.
- `GET /cases/{id}`: case details including evidence_json and explanation_json.
- `POST /cases/{id}/decision`: human reviewer decisions by case type:
  - `SEAT_OVER_CAP`: ACCEPT_EXCEPTION | REQUEST_CORRECTION (FR-11)
  - `DUP_FUZZY` / `DUP_EXACT_CROSS`: CONFIRM_DUPLICATE | NOT_DUPLICATE |
    ESCALATE | REQUEST_CORRECTION
  - `ANOMALY`: ACKNOWLEDGE | ESCALATE | DISMISS
  - `RING`: CONFIRM | DISMISS | ESCALATE
  - `TIMELINE`: ACKNOWLEDGE | REQUEST_CORRECTION | DISMISS
  Decisions on ML duplicate cases append labels to `ml_feedback`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import ApiError
from app.core.events import event_bus
from app.core.rbac import AICTE_ROLES, INSTITUTION_ROLES, require_roles, scope_cases
from app.db.models import Case, CaseStatus, CaseType, MlFeedback, User
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
    action: Literal[
        "ACCEPT_EXCEPTION",
        "REQUEST_CORRECTION",
        "CONFIRM_DUPLICATE",
        "NOT_DUPLICATE",
        "ACKNOWLEDGE",
        "CONFIRM",
        "DISMISS",
        "ESCALATE",
    ]
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


@router.post("/{case_id}/decision", summary="Human decision on a case (FR-11, FR-17b)")
def decide_case(
    case_id: int,
    body: Decision,
    user: User = Depends(require_roles(*AICTE_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    case = db.scalar(scope_cases(select(Case).where(Case.id == case_id), user))
    if case is None:
        raise _not_found()

    if case.status not in (CaseStatus.OPEN, CaseStatus.ESCALATED):
        raise ApiError(409, "CASE_ALREADY_DECIDED", "This case has already been decided.")

    now = datetime.now(UTC)

    # 1. Seat Over-Cap decision
    if case.type == CaseType.SEAT_OVER_CAP:
        if body.action not in ("ACCEPT_EXCEPTION", "REQUEST_CORRECTION"):
            raise ApiError(422, "ACTION_INVALID", "Invalid action for SEAT_OVER_CAP case.")
        result = seat_ledger.decide_over_cap_case(
            db, case, body.action, body.note, user, record_ids=body.record_ids
        )

    # 2. Duplicate decision (Fuzzy or Exact Cross)
    elif case.type in (CaseType.DUP_FUZZY, CaseType.DUP_EXACT_CROSS):
        if body.action == "CONFIRM_DUPLICATE" or body.action == "CONFIRM":
            case.status = CaseStatus.CONFIRMED
        elif body.action == "NOT_DUPLICATE" or body.action == "DISMISS":
            case.status = CaseStatus.DISMISSED
        elif body.action == "ESCALATE":
            case.status = CaseStatus.ESCALATED
        elif body.action in ("ACCEPT_EXCEPTION",):
            raise ApiError(
                400,
                "CASE_TYPE_UNSUPPORTED",
                "ACCEPT_EXCEPTION is only valid for SEAT_OVER_CAP cases.",
                "errors.case_type_unsupported",
            )
        else:
            raise ApiError(
                422, "ACTION_INVALID", f"Invalid action {body.action} for duplicate case."
            )

        case.decided_by = user.id
        case.decided_at = now
        case.decision_note = body.note

        # Log ML feedback for duplicate cases if features exist
        feats = (case.evidence_json or {}).get("features")
        label = "CONFIRMED" if case.status == CaseStatus.CONFIRMED else "DISMISSED"
        db.add(MlFeedback(case_id=case.id, label=label, features_json=feats))
        result = {"case_status": case.status.value, "feedback_recorded": bool(feats)}

    # 3. Anomaly decision
    elif case.type == CaseType.ANOMALY:
        if body.action in ("ACKNOWLEDGE", "DISMISS"):
            case.status = CaseStatus.RESOLVED
        elif body.action == "ESCALATE":
            case.status = CaseStatus.ESCALATED
        else:
            raise ApiError(422, "ACTION_INVALID", f"Invalid action {body.action} for anomaly case.")

        case.decided_by = user.id
        case.decided_at = now
        case.decision_note = body.note
        result = {"case_status": case.status.value}

    # 4. Ring decision
    elif case.type == CaseType.RING:
        if body.action == "CONFIRM":
            case.status = CaseStatus.CONFIRMED
        elif body.action == "DISMISS":
            case.status = CaseStatus.DISMISSED
        elif body.action == "ESCALATE":
            case.status = CaseStatus.ESCALATED
        else:
            raise ApiError(422, "ACTION_INVALID", f"Invalid action {body.action} for ring case.")

        case.decided_by = user.id
        case.decided_at = now
        case.decision_note = body.note
        result = {"case_status": case.status.value}

    # 5. Timeline / Other decision
    elif case.type in (CaseType.TIMELINE, CaseType.RULE_WARNING, CaseType.STUDENT_DISPUTE):
        if body.action in ("ACKNOWLEDGE", "REQUEST_CORRECTION", "DISMISS"):
            case.status = CaseStatus.RESOLVED
        elif body.action == "ESCALATE":
            case.status = CaseStatus.ESCALATED
        else:
            case.status = CaseStatus.CONFIRMED

        case.decided_by = user.id
        case.decided_at = now
        case.decision_note = body.note
        result = {"case_status": case.status.value}
    else:
        raise ApiError(400, "CASE_TYPE_UNSUPPORTED", f"Unsupported case type {case.type}")

    audit.record(
        db,
        "CASE_DECISION",
        actor=user,
        entity_type="case",
        entity_id=case.id,
        detail={
            "case_type": str(case.type),
            "action": body.action,
            "case_status": case.status.value,
            "note": body.note,
        },
    )
    db.commit()
    db.refresh(case)
    event_bus.publish_threadsafe(
        "case_updated",
        {"case_id": case.id, "case_type": str(case.type), "status": case.status.value},
    )
    return {**result, "case": CaseOut.model_validate(case).model_dump(mode="json")}
