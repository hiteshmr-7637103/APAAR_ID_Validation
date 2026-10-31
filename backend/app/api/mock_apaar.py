"""GET /mock/apaar/{apaar_id}: the **MOCK** APAAR registry (spec 2.2, FR-40).

Synthetic records only (name, DOB, gender). It simulates the registry API that AICTE's
backend would call, so it is restricted to AICTE roles; institution users get registry
results only through validation messages (Phase 3), never raw registry data.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.rbac import AICTE_ROLES, require_roles
from app.db.models import User
from app.db.session import get_db
from app.services.apaar_client import get_apaar_client, is_valid_apaar_format

router = APIRouter(prefix="/mock/apaar", tags=["mock"])

MOCK_NOTICE = "MOCK APAAR registry: synthetic records only, not the national APAAR system."


class ApaarRecordOut(BaseModel):
    apaar_id: str
    full_name: str
    dob: date
    gender: str
    source: str
    notice: str = MOCK_NOTICE


@router.get("/{apaar_id}", response_model=ApaarRecordOut, summary="MOCK APAAR registry lookup")
def lookup_apaar(
    apaar_id: str,
    _: User = Depends(require_roles(*AICTE_ROLES)),
    db: Session = Depends(get_db),
):
    if not is_valid_apaar_format(apaar_id):
        raise ApiError(
            422,
            "APAAR_INVALID",
            "An APAAR ID must be exactly 12 digits.",
            "validation.apaar_invalid",
            {"apaar_id": apaar_id, "source": "MOCK"},
        )
    rec = get_apaar_client(db).lookup(apaar_id)
    if rec is None:
        raise ApiError(
            404,
            "APAAR_NOT_FOUND",
            "This APAAR ID is not in the (MOCK) registry.",
            "validation.apaar_not_found",
            {"source": "MOCK"},
        )
    return ApaarRecordOut(**rec.__dict__)
