"""Dashboards (spec 8, FR-20, FR-21, FR-22, FR-23, FR-25).

Endpoints:
- `GET /dashboards/aicte`: National overview (enrolment vs intake, over-cap, coverage,
  trust tiers, cases summary, risk leaderboard). AICTE-only.
- `GET /dashboards/institution/{id}`: Seat utilisation per bucket with caps and excess,
  trust-tier split, record status, submission status, coverage meter, Data Health Score,
  recent errors, and pending cases.
- `GET /dashboards/coverage`: Coverage breakdown (national or institution-scoped).
- `GET /dashboards/institutions/{id}/coverage`: Programme-level coverage meter for an institution.
- `GET /dashboards/institutions/{id}/health-score`: Data Health Score (0-100).
- `GET /dashboards/institutions/risk-ranking`: Ranked institutions by risk score. AICTE-only.
- `GET /dashboards/trust-tiers?institution_id=`: Enrolment by trust tier.
- `GET /dashboards/events/stream`: Server-Sent Events (SSE) live updates stream.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.institutions import ensure_visible, seat_rows
from app.core.config import settings
from app.core.errors import ApiError
from app.core.events import event_bus
from app.core.rbac import (
    AICTE_ROLES,
    INSTITUTION_ROLES,
    require_roles,
    scope_cases,
    scope_to_institution,
)
from app.db.models import (
    BatchStatus,
    Case,
    CaseStatus,
    Institution,
    InstitutionProgramme,
    LedgerKey,
    RecordStatus,
    Severity,
    StudentRecord,
    UploadBatch,
    User,
    ValidationResult,
)
from app.db.session import get_db
from app.services import reports, uploads

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
_STAFF = require_roles(*AICTE_ROLES, *INSTITUTION_ROLES)
_AICTE_ONLY = require_roles(*AICTE_ROLES)
TIERS = (0, 1, 2, 3)


def tier_split(db: Session, user: User, institution_id: int | None, year: str | None):
    """Records by trust tier (rejected records excluded)."""
    stmt = (
        select(StudentRecord.trust_tier, func.count())
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .where(StudentRecord.status != RecordStatus.REJECTED)
        .group_by(StudentRecord.trust_tier)
    )
    if year:
        stmt = stmt.where(InstitutionProgramme.academic_year == year)
    if institution_id is not None:
        stmt = stmt.where(StudentRecord.institution_id == institution_id)
    stmt = scope_to_institution(stmt, user, StudentRecord.institution_id)
    counts = dict(db.execute(stmt).all())
    total = sum(counts.values())
    by_tier = {f"T{t}": counts.get(t, 0) for t in TIERS}
    return {
        "total": total,
        "by_tier": by_tier,
        "trust_ratio_t3": round(by_tier["T3"] / total, 3) if total else None,
        "summary_en": (
            f"{total} reported, {by_tier['T3']} at T3, {by_tier['T2']} at T2, "
            f"{by_tier['T1']} at T1, {by_tier['T0']} at T0"
        ),
        "note": "T3 (student confirmation) arrives in Phase 7.",
    }


@router.get("/trust-tiers", summary="Enrolment by trust tier (FR-20)")
def trust_tiers(
    institution_id: int | None = None,
    academic_year: str | None = None,
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if institution_id is not None:
        ensure_visible(db, user, institution_id)
    elif user.role in INSTITUTION_ROLES:
        institution_id = user.institution_id
    year = academic_year or settings.current_academic_year
    return {
        "institution_id": institution_id,
        "academic_year": year,
        **tier_split(db, user, institution_id, year),
    }


@router.get("/aicte", summary="AICTE national overview dashboard (FR-22)")
def aicte_dashboard(
    academic_year: str | None = None,
    state: str | None = None,
    user: User = Depends(_AICTE_ONLY),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    year = academic_year or settings.current_academic_year
    return reports.compute_aicte_kpis(db, year, state=state)


@router.get("/coverage", summary="Headcount coverage distribution (FR-21)")
def coverage_distribution(
    academic_year: str | None = None,
    state: str | None = None,
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    year = academic_year or settings.current_academic_year
    if user.role in INSTITUTION_ROLES:
        return reports.compute_institution_coverage(db, user.institution_id, year)
    return reports.compute_national_coverage(db, year, state=state)


@router.get(
    "/institutions/{institution_id}/coverage",
    summary="Institution coverage meter (FR-21)",
)
def institution_coverage(
    institution_id: int,
    academic_year: str | None = None,
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_visible(db, user, institution_id)
    year = academic_year or settings.current_academic_year
    return reports.compute_institution_coverage(db, institution_id, year)


@router.get(
    "/institutions/{institution_id}/health-score",
    summary="Institution Data Health Score (FR-23)",
)
def institution_health_score(
    institution_id: int,
    academic_year: str | None = None,
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_visible(db, user, institution_id)
    year = academic_year or settings.current_academic_year
    return reports.compute_data_health_score(db, institution_id, year)


@router.get(
    "/institutions/risk-ranking",
    summary="AICTE institution risk ranking (FR-25)",
)
def risk_ranking(
    academic_year: str | None = None,
    state: str | None = None,
    user: User = Depends(_AICTE_ONLY),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    year = academic_year or settings.current_academic_year
    return reports.compute_risk_ranking(db, year, state=state)


@router.get("/institution/{institution_id}", summary="Institution dashboard (FR-23)")
def institution_dashboard(
    institution_id: int,
    academic_year: str | None = None,
    errors_limit: int = Query(20, ge=1, le=200),
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_visible(db, user, institution_id)
    inst = db.get(Institution, institution_id)
    if inst is None:  # pragma: no cover - ensure_visible already checked
        raise ApiError(404, "INSTITUTION_NOT_FOUND", "Institution not found.")
    year = academic_year or settings.current_academic_year

    programmes = seat_rows(db, institution_id, year)
    totals = {k.value: {"ledger_key": k.value, "cap": 0, "used": 0, "excess": 0} for k in LedgerKey}
    for p in programmes:
        for b in p["buckets"]:
            t = totals[b["ledger_key"]]
            t["cap"] += b["cap"]
            t["used"] += b["used"]
            t["excess"] += b["excess"]

    sr = StudentRecord
    in_year = (
        select(sr.id)
        .join(InstitutionProgramme, InstitutionProgramme.id == sr.institution_programme_id)
        .where(
            sr.institution_id == institution_id,
            InstitutionProgramme.academic_year == year,
        )
    )
    status_counts = dict(
        db.execute(
            select(sr.status, func.count()).where(sr.id.in_(in_year)).group_by(sr.status)
        ).all()
    )
    flagged = db.scalar(
        select(func.count()).where(
            sr.id.in_(in_year),
            sr.over_cap.is_(True),
            StudentRecord.status != RecordStatus.REJECTED,
        )
    )
    flagged_unreviewed = db.scalar(
        select(func.count()).where(
            sr.id.in_(in_year),
            sr.over_cap.is_(True),
            sr.over_cap_reviewed.is_(False),
            StudentRecord.status != RecordStatus.REJECTED,
        )
    )

    batch_counts = dict(
        db.execute(
            select(UploadBatch.status, func.count())
            .where(UploadBatch.institution_id == institution_id)
            .group_by(UploadBatch.status)
        ).all()
    )
    recent_batches = db.scalars(
        select(UploadBatch)
        .where(UploadBatch.institution_id == institution_id)
        .order_by(UploadBatch.id.desc())
        .limit(10)
    ).all()

    recent_errors = db.execute(
        select(
            ValidationResult.batch_id,
            ValidationResult.row_number,
            ValidationResult.student_record_id,
            ValidationResult.code,
            ValidationResult.field,
            ValidationResult.message_en,
            ValidationResult.suggested_fix_json,
        )
        .join(UploadBatch, UploadBatch.id == ValidationResult.batch_id)
        .where(
            UploadBatch.institution_id == institution_id,
            ValidationResult.severity == Severity.ERROR,
            ValidationResult.resolved.is_(False),
        )
        .order_by(ValidationResult.id.desc())
        .limit(errors_limit)
    ).all()

    cases = db.scalars(
        scope_cases(select(Case), user)
        .where(Case.institution_id == institution_id, Case.status == CaseStatus.OPEN)
        .order_by(Case.priority, Case.id.desc())
        .limit(50)
    ).all()

    coverage_data = reports.compute_institution_coverage(db, institution_id, year)
    health_score_data = reports.compute_data_health_score(db, institution_id, year)

    return {
        "institution": {
            "id": inst.id,
            "code": inst.code,
            "name": inst.name,
            "state": inst.state,
        },
        "academic_year": year,
        "seats": {"totals": list(totals.values()), "programmes": programmes},
        "over_cap": {
            "flagged_records": flagged,
            "awaiting_aicte_review": flagged_unreviewed,
        },
        "trust_tiers": tier_split(db, user, institution_id, year),
        "records_by_status": {s.value: status_counts.get(s, 0) for s in RecordStatus},
        "submissions": {
            "by_status": {s.value: batch_counts.get(s, 0) for s in BatchStatus},
            "pending_approval": batch_counts.get(BatchStatus.PENDING_APPROVAL, 0),
            "recent": [uploads.batch_dict(db, b) for b in recent_batches],
        },
        "coverage_meter": coverage_data,
        "data_health_score": health_score_data,
        "recent_errors": [
            {
                "batch_id": e.batch_id,
                "row": e.row_number,
                "student_record_id": e.student_record_id,
                "code": e.code,
                "field": e.field,
                "message_en": e.message_en,
                "suggested_fix": e.suggested_fix_json,
            }
            for e in recent_errors
        ],
        "pending_cases": [
            {
                "id": c.id,
                "type": str(c.type),
                "visibility": str(c.visibility),
                "priority": c.priority,
                "summary_en": (c.explanation_json or {}).get("summary_en"),
                "created_at": c.created_at.isoformat(),
            }
            for c in cases
        ],
    }


@router.get("/events/stream", summary="Live updates SSE stream (FR-22)")
async def sse_event_stream(
    request: Request,
    user: User = Depends(_STAFF),
):
    async def event_generator():
        q = event_bus.subscribe()
        try:
            yield 'event: ping\ndata: {"status": "connected"}\n\n'
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                except TimeoutError:
                    yield 'event: ping\ndata: {"status": "keepalive"}\n\n'
        finally:
            event_bus.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
