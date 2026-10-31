"""POST /validate/record: dry-run the rule engine on one record (spec 8, FR-6 to FR-13).

Nothing is written. Besides the rule outcomes the response carries:
- `apaar_status`: booleans from the MOCK registry check (format ok, found, name match,
  DOB match) and never registry data, so institution users cannot read registry contents;
- `seat_projection`: a read-only preview of the bucket the record would land in ("would
  exceed LATERAL by 2"), without taking a seat (P4: "dry-run shows it").
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.core.rbac import AICTE_ROLES, INSTITUTION_ROLES, require_roles
from app.db.models import Institution, User
from app.db.session import get_db
from app.services import seat_ledger
from app.services.rule_engine import DbRuleContext, RecordInput, validate_record

router = APIRouter(tags=["validation"])

_STR = dict(default=None, max_length=255)


class StudentRecordIn(BaseModel):
    """Spec 7.2 CSV columns (+ academic year, and the institution for AICTE callers).
    Codes are plain strings so the rule engine can explain an invalid value."""

    model_config = ConfigDict(str_strip_whitespace=True)

    institution_id: int | None = Field(None, description="AICTE callers only")
    apaar_id: str | None = Field(None, max_length=32)
    student_college_id: str | None = Field(None, max_length=64)
    full_name: str | None = Field(**_STR)
    dob: date | None = None
    gender: str | None = Field(None, max_length=8)
    programme_code: str | None = Field(None, max_length=64)
    shift: str | None = Field("REGULAR", max_length=16)
    academic_year: str | None = Field(None, pattern=r"^\d{4}-\d{2}$")
    admission_date: date | None = None
    admission_category: str | None = Field(None, max_length=16)
    entry_type: str | None = Field("REGULAR", max_length=16)
    mode: str | None = Field("FULL_TIME", max_length=16)
    nationality: str | None = Field("Indian", max_length=64)
    is_nri: bool = False
    nri_sponsor: str | None = Field(**_STR)
    is_oci: bool = False
    oci_number: str | None = Field(None, max_length=64)
    is_foreign_national: bool = False
    id_document_note: str | None = Field(**_STR)
    phone: str | None = Field(None, max_length=32)
    email: str | None = Field(None, max_length=255)
    address: str | None = Field(None, max_length=500)

    @field_validator("*", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator(
        "gender", "programme_code", "shift", "admission_category", "entry_type", "mode"
    )
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) else v


def resolve_institution(db: Session, user: User, requested: int | None) -> int:
    """Institution users act for their own institution only (404 for any other, D7);
    AICTE users must name one."""
    if user.role in INSTITUTION_ROLES:
        if requested is not None and requested != user.institution_id:
            raise ApiError(
                404,
                "INSTITUTION_NOT_FOUND",
                "Institution not found.",
                "errors.institution_not_found",
            )
        return user.institution_id
    if requested is None or db.get(Institution, requested) is None:
        raise ApiError(
            422,
            "INSTITUTION_REQUIRED",
            "Choose the institution the record belongs to.",
            "errors.institution_required",
        )
    return requested


def to_record_input(body: StudentRecordIn, institution_id: int) -> RecordInput:
    data = body.model_dump(exclude={"institution_id"})
    data["academic_year"] = data["academic_year"] or settings.current_academic_year
    return RecordInput(institution_id=institution_id, **data)


def report_body(db: Session, report, inp: RecordInput, ctx: DbRuleContext) -> dict[str, Any]:
    projection = None
    if report.ip is not None and report.ledger_key is not None:
        meta = seat_ledger.ip_meta(db, report.ip.id)
        projection = seat_ledger.projection(db, meta, report.ledger_key, ctx.rules)
    return {
        "institution_id": inp.institution_id,
        "academic_year": report.ip.academic_year if report.ip else inp.academic_year,
        "programme": (
            {
                "institution_programme_id": report.ip.id,
                "programme_code": report.ip.programme_code,
                "name": report.ip.programme_name,
                "shift": report.ip.shift,
                "level": report.ip.level,
            }
            if report.ip
            else None
        ),
        "ledger_key": str(report.ledger_key) if report.ledger_key else None,
        "has_error": report.has_error,
        "counts": report.counts(),
        "outcomes": [o.to_dict() for o in report.outcomes],
        "apaar_status": report.apaar_status,
        "seat_projection": projection,
    }


@router.post("/validate/record", summary="Dry-run the rule engine on one record (no write)")
def validate_one(
    body: StudentRecordIn,
    user: User = Depends(require_roles(*AICTE_ROLES, *INSTITUTION_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    inp = to_record_input(body, resolve_institution(db, user, body.institution_id))
    ctx = DbRuleContext(db)
    report = validate_record(inp, ctx)
    out = report_body(db, report, inp, ctx)
    db.rollback()  # strictly read-only
    return out


@router.get("/validate/codes", summary="Rule codes, severities and message keys")
def rule_codes(_: User = Depends(require_roles(*AICTE_ROLES, *INSTITUTION_ROLES))):
    from app.services.messages import MESSAGES_EN
    from app.services.rule_engine import RULE_CODES

    return {
        "codes": {code: str(sev) for code, sev in RULE_CODES.items()},
        "messages_en": MESSAGES_EN,
    }
