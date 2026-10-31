"""Rules-as-data admin (spec 7.3, 8; P21): GET /rules, PUT /rules/{key} (AICTE_ADMIN).

Every PUT appends a new version (never edits one in place). When the new version of a
bucket rule is already effective, every Seat Ledger cap is recomputed at once, re-deriving
over-cap flags and cases; a future-dated version takes effect at the first admission into
a bucket after its `effective_from` (or on the next PUT). No code change is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import ApiError
from app.core.rbac import require_roles
from app.db.models import QuotaRule, Role, User
from app.db.session import get_db
from app.services import seat_ledger
from app.services.quota import (
    CAP_RULE_KEYS,
    DEFAULT_QUOTA_RULES,
    active_rules,
    validate_rule_value,
)

router = APIRouter(prefix="/rules", tags=["rules"])
_ADMIN = require_roles(Role.AICTE_ADMIN)


class RuleUpdate(BaseModel):
    value_json: dict[str, Any]
    description: str | None = Field(None, max_length=2000)
    effective_from: datetime | None = Field(None, description="Default: now")


def _version(r: QuotaRule) -> dict[str, Any]:
    return {
        "version": r.version,
        "value_json": r.value_json,
        "description": r.description,
        "effective_from": r.effective_from,
        "updated_by": r.updated_by,
    }


@router.get("", summary="Active quota/rule values with version history")
def list_rules(_: User = Depends(_ADMIN), db: Session = Depends(get_db)) -> list[dict]:
    active = active_rules(db)
    rows = db.scalars(select(QuotaRule).order_by(QuotaRule.key, QuotaRule.version.desc())).all()
    by_key: dict[str, list[QuotaRule]] = {}
    for r in rows:
        by_key.setdefault(r.key, []).append(r)
    now = datetime.now(UTC)
    out = []
    for key, (default, desc) in DEFAULT_QUOTA_RULES.items():
        versions = by_key.get(key, [])
        current = next((v for v in versions if v.effective_from <= now), None)
        out.append(
            {
                "key": key,
                "description": (current.description if current else None) or desc,
                "active_value": active[key],
                "active_version": current.version if current else None,
                "is_default": current is None,
                "verify": bool(isinstance(default, dict) and default.get("verify")),
                "versions": [_version(v) for v in versions],
            }
        )
    return out


@router.put("/{key}", summary="Append a new version of a rule (recomputes seat caps)")
def update_rule(
    key: str,
    body: RuleUpdate,
    user: User = Depends(_ADMIN),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if key not in DEFAULT_QUOTA_RULES:
        raise ApiError(404, "RULE_NOT_FOUND", f"Unknown rule {key!r}.", "errors.rule_not_found")
    problems = validate_rule_value(key, body.value_json)
    if problems:
        raise ApiError(
            422,
            "RULE_INVALID",
            "The rule value is not valid.",
            "errors.rule_invalid",
            {"problems": problems},
        )
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"quota_rules:{key}"))))
    latest = db.scalar(select(func.max(QuotaRule.version)).where(QuotaRule.key == key)) or 0
    prev_desc = db.scalar(
        select(QuotaRule.description).where(QuotaRule.key == key, QuotaRule.version == latest)
    )
    rule = QuotaRule(
        key=key,
        value_json=body.value_json,
        description=body.description or prev_desc or DEFAULT_QUOTA_RULES[key][1],
        version=latest + 1,
        updated_by=user.id,
    )
    if body.effective_from is not None:
        rule.effective_from = body.effective_from
    db.add(rule)
    db.flush()
    db.refresh(rule)
    recompute = None
    if key in CAP_RULE_KEYS:
        recompute = seat_ledger.recompute_caps(db)
    audit.record(
        db,
        "RULE_UPDATE",
        actor=user,
        entity_type="quota_rule",
        entity_id=key,
        detail={
            "version": rule.version,
            "value_json": body.value_json,
            "effective_from": rule.effective_from.isoformat(),
            "recompute": recompute,
        },
    )
    db.commit()
    return {"key": key, **_version(rule), "recompute": recompute}
