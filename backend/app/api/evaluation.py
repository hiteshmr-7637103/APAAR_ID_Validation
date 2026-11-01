"""Evaluation API endpoints (spec Section 8, 14, 15 Phase 8).

Exposes:
- GET /evaluation: retrieve latest cached or stored evaluation report.
- POST /evaluation/run: execute full evaluation suite across all 7 criteria,
  update docs/evaluation.md, and return metrics.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.rbac import AICTE_ROLES, require_roles
from app.db.models import User
from app.db.session import get_db
from app.eval.run_eval import run_evaluation

router = APIRouter(prefix="/evaluation", tags=["evaluation"])
_AICTE_OR_ADMIN = require_roles(*AICTE_ROLES)

# In-memory cache for latest evaluation results
_LATEST_EVALUATION: dict[str, Any] | None = None


@router.get("", summary="Get latest system evaluation metrics (FR-14)")
def get_evaluation(
    user: User = Depends(_AICTE_OR_ADMIN),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    global _LATEST_EVALUATION
    if _LATEST_EVALUATION is None:
        _LATEST_EVALUATION = run_evaluation(db)
    return _LATEST_EVALUATION


@router.post("/run", summary="Trigger live evaluation run across all 7 benchmark criteria")
def trigger_evaluation_run(
    user: User = Depends(_AICTE_OR_ADMIN),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    global _LATEST_EVALUATION
    results = run_evaluation(db)
    _LATEST_EVALUATION = results
    return results
