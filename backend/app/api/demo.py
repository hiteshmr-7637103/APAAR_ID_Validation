"""Demo tooling (spec 5.10). Phase 2: POST /demo/seed (FR-41).

Later phases add /demo/inject/{scenario}, /demo/retrain, /demo/concurrency-test and
/demo/tamper-audit here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.config import settings
from app.core.rbac import require_roles
from app.db.models import Role, User
from app.db.session import get_db
from app.synth.generator import GeneratorConfig
from app.synth.loader import seed_national

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/seed", summary="Regenerate the deterministic synthetic national dataset")
def demo_seed(
    seed: int = Query(settings.synth_seed, description="Random seed (default 42)"),
    institutions: int = Query(settings.synth_institutions, ge=4, le=200),
    students: int = Query(settings.synth_students, ge=500, le=50000),
    user: User = Depends(require_roles(Role.AICTE_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """Wipes all generated data (students, records, ledger, cases, consents, MOCK registry,
    labels, quota rules) and rebuilds it. Demo accounts and demo institutions are kept.
    Synchronous: about 20 s for the default 40 institutions / 20,000 students."""
    config = GeneratorConfig(seed=seed, n_institutions=institutions, target_students=students)
    summary = seed_national(db, config)
    audit.record(
        db,
        "DEMO_SEED",
        actor=user,
        entity_type="dataset",
        entity_id=seed,
        detail={"institutions": institutions, "students": students, "seed": seed},
    )
    db.commit()
    return summary


@router.post("/retrain", summary="Retrain duplicate matcher with reviewer feedback (FR-17b)")
def demo_retrain(
    user: User = Depends(require_roles(Role.AICTE_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    from app.ml.train import retrain_with_feedback

    report = retrain_with_feedback(db)
    audit.record(
        db,
        "ML_RETRAIN",
        actor=user,
        entity_type="model",
        entity_id=1,
        detail=report,
    )
    db.commit()
    return {"status": "ok", "report": report}


@router.post("/triage", summary="Trigger full ML triage (duplicates, rings, anomalies)")
def demo_triage(
    user: User = Depends(require_roles(Role.AICTE_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    from app.db.models import StudentRecord
    from app.services import triage

    rec_ids = list(db.scalars(select(StudentRecord.id).limit(10000)).all())
    dup_cases = triage.triage_duplicate_records(db, rec_ids)
    ring_cases = triage.triage_rings(db)
    anom_cases = triage.triage_anomalies(db)
    db.commit()
    return {
        "status": "ok",
        "duplicate_cases_opened": len(dup_cases),
        "ring_cases_opened": len(ring_cases),
        "anomaly_cases_opened": len(anom_cases),
    }
