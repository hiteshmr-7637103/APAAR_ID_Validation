"""Institutions, programmes and (Phase 3) their Seat Ledger view.

Institution users are scoped to their own institution via `scope_to_institution`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import InstitutionDetail, InstitutionOut, InstitutionProgrammeOut, ProgrammeOut
from app.core.config import settings
from app.core.errors import ApiError
from app.core.rbac import AICTE_ROLES, INSTITUTION_ROLES, require_roles, scope_to_institution
from app.db.models import (
    Institution,
    InstitutionProgramme,
    LedgerKey,
    Programme,
    RecordStatus,
    SeatLedgerBalance,
    StudentRecord,
    User,
)
from app.db.session import get_db
from app.services import seat_ledger
from app.services.quota import active_rules

router = APIRouter(tags=["institutions"])

_STAFF = require_roles(*AICTE_ROLES, *INSTITUTION_ROLES)


def _not_found() -> ApiError:
    # 404 (not 403) for other institutions' rows so their existence is not disclosed.
    return ApiError(
        404, "INSTITUTION_NOT_FOUND", "Institution not found.", "errors.institution_not_found"
    )


@router.get("/institutions", response_model=list[InstitutionOut])
def list_institutions(user: User = Depends(_STAFF), db: Session = Depends(get_db)):
    stmt = scope_to_institution(select(Institution), user, Institution.id).order_by(
        Institution.code
    )
    return db.scalars(stmt).all()


@router.get("/institutions/{institution_id}", response_model=InstitutionDetail)
def get_institution(
    institution_id: int, user: User = Depends(_STAFF), db: Session = Depends(get_db)
):
    stmt = scope_to_institution(
        select(Institution)
        .where(Institution.id == institution_id)
        .options(selectinload(Institution.programmes).selectinload(InstitutionProgramme.programme)),
        user,
        Institution.id,
    )
    inst = db.scalar(stmt)
    if inst is None:
        raise _not_found()
    return inst


@router.get(
    "/institutions/{institution_id}/programmes", response_model=list[InstitutionProgrammeOut]
)
def list_institution_programmes(
    institution_id: int, user: User = Depends(_STAFF), db: Session = Depends(get_db)
):
    visible = scope_to_institution(
        select(Institution.id).where(Institution.id == institution_id), user, Institution.id
    )
    if db.scalar(visible) is None:
        raise _not_found()
    stmt = scope_to_institution(
        select(InstitutionProgramme)
        .where(InstitutionProgramme.institution_id == institution_id)
        .options(selectinload(InstitutionProgramme.programme))
        .order_by(InstitutionProgramme.id),
        user,
        InstitutionProgramme.institution_id,
    )
    return db.scalars(stmt).all()


@router.get("/programmes", response_model=list[ProgrammeOut])
def list_programmes(_: User = Depends(_STAFF), db: Session = Depends(get_db)):
    """Canonical programme catalogue (not institution-owned data)."""
    return db.scalars(select(Programme).order_by(Programme.canonical_code)).all()


@router.get("/institutions/{institution_id}/seats", summary="Seat buckets, caps, used, excess")
def institution_seats(
    institution_id: int,
    academic_year: str | None = None,
    user: User = Depends(_STAFF),
    db: Session = Depends(get_db),
):
    """Seat Ledger view per programme row (spec 8): every bucket with cap, used and excess,
    plus records received vs declared enrolment (coverage, FR-21). Own data only."""
    ensure_visible(db, user, institution_id)
    return seat_rows(db, institution_id, academic_year or settings.current_academic_year)


def ensure_visible(db: Session, user: User, institution_id: int) -> None:
    visible = scope_to_institution(
        select(Institution.id).where(Institution.id == institution_id), user, Institution.id
    )
    if db.scalar(visible) is None:
        raise _not_found()


def seat_rows(db: Session, institution_id: int, year: str) -> list[dict]:
    """Every programme row of the institution for `year` with all its buckets."""
    rules = active_rules(db)
    metas = {
        m.id: m for m in seat_ledger.all_ip_meta(db).values() if m.institution_id == institution_id
    }
    ips = db.scalars(
        select(InstitutionProgramme)
        .where(
            InstitutionProgramme.institution_id == institution_id,
            InstitutionProgramme.academic_year == year,
        )
        .options(selectinload(InstitutionProgramme.programme))
        .order_by(InstitutionProgramme.id)
    ).all()
    ids = [ip.id for ip in ips]
    balances = {
        (b.institution_programme_id, str(b.ledger_key)): b
        for b in db.scalars(
            select(SeatLedgerBalance).where(SeatLedgerBalance.institution_programme_id.in_(ids))
        )
    }
    received = dict(
        db.execute(
            select(StudentRecord.institution_programme_id, func.count())
            .where(
                StudentRecord.institution_programme_id.in_(ids),
                StudentRecord.status != RecordStatus.REJECTED,
            )
            .group_by(StudentRecord.institution_programme_id)
        ).all()
    )
    out = []
    for ip in ips:
        buckets = []
        for key in LedgerKey:
            bal = balances.get((ip.id, key.value))
            cap = bal.cap if bal else seat_ledger.cap_for(metas[ip.id], key, rules)
            used = bal.used if bal else 0
            buckets.append(
                {"ledger_key": key.value, "cap": cap, "used": used, "excess": max(0, used - cap)}
            )
        got = received.get(ip.id, 0)
        out.append(
            {
                "institution_programme_id": ip.id,
                "programme_code": ip.programme.canonical_code,
                "programme_name": ip.programme.name,
                "academic_year": ip.academic_year,
                "shift": ip.shift.value,
                "approved_intake": ip.approved_intake,
                "declared_enrolment": ip.declared_enrolment,
                "records_received": got,
                "coverage": (
                    round(got / ip.declared_enrolment, 3) if ip.declared_enrolment else None
                ),
                "buckets": buckets,
            }
        )
    return out
