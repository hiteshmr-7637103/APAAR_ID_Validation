"""Report export endpoints (spec 5.6, FR-24; spec 8).

Generates CSV downloads and JSON previews for AICTE regulatory reports:
1. `enrolment-vs-intake`
2. `supernumerary-utilisation`
3. `over-cap-flags`
4. `cross-institution-duplicates`
5. `coverage`
6. `risk-ranking`
7. `trust-tiers`
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.core.rbac import AICTE_ROLES, require_roles
from app.db.models import User
from app.db.session import get_db
from app.services import reports as report_service

router = APIRouter(prefix="/reports", tags=["reports"])
_AICTE_STAFF = require_roles(*AICTE_ROLES)

REPORT_METADATA = [
    {
        "name": "enrolment-vs-intake",
        "title": "Enrolment vs Approved Intake",
        "description": "Approved intake vs actual non-rejected enrolments per programme and shift.",
    },
    {
        "name": "supernumerary-utilisation",
        "title": "Supernumerary Utilisation",
        "description": (
            "Utilisation breakdown across APPROVED, FN/OCI, TFW, NRI, and LATERAL buckets."
        ),
    },
    {
        "name": "over-cap-flags",
        "title": "Over-Cap Flags",
        "description": "All seats exceeding approved caps with excess amounts and case status.",
    },
    {
        "name": "cross-institution-duplicates",
        "title": "Cross-Institution Duplicates",
        "description": "Cross-institution duplicate pairs and multi-enrolment matches.",
    },
    {
        "name": "coverage",
        "title": "Headcount Coverage",
        "description": (
            "Declared headcount vs actual received records with status band (RED/AMBER/GREEN)."
        ),
    },
    {
        "name": "risk-ranking",
        "title": "Institution Risk Ranking",
        "description": "Composite risk scores and transparent 4-factor component breakdown.",
    },
    {
        "name": "trust-tiers",
        "title": "Trust Tier Distribution",
        "description": "T0-T3 student trust tier distribution and T3 verification ratio.",
    },
]


@router.get("", summary="List available regulatory reports (FR-24)")
def list_reports(
    user: User = Depends(_AICTE_STAFF),
) -> dict[str, Any]:
    return {"reports": REPORT_METADATA}


@router.get("/{name}", summary="Generate report in CSV or JSON format (FR-24)")
def get_report(
    name: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    academic_year: str | None = None,
    state: str | None = None,
    user: User = Depends(_AICTE_STAFF),
    db: Session = Depends(get_db),
) -> Any:
    valid_names = set(report_service.REPORT_GENERATORS.keys())
    if name not in valid_names:
        raise ApiError(
            404,
            "REPORT_NOT_FOUND",
            f"Report '{name}' not found. Available reports: {', '.join(sorted(valid_names))}",
        )

    year = academic_year or settings.current_academic_year

    if format == "csv":
        csv_data = report_service.generate_report_csv(db, name, year, state=state)
        sanitized_year = year.replace("/", "-")
        state_suffix = f"_{state}" if state else ""
        filename = f"{name}_{sanitized_year}{state_suffix}.csv"
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return report_service.generate_report_json(db, name, year, state=state)
