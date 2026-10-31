"""Persist AICTE-only cases produced by the rule engine (DUP_EXACT_CROSS, TIMELINE).

A case is identified by its dedupe key (`subject_ids_json.key`, built from the HMAC APAAR
token); a new finding with the same key is merged into the open case instead of opening a
twin (a partial unique index enforces one OPEN case per key). Cases never change a
record's status: a human decides (spec 1, FR-14, NFR-8).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Case, CaseStatus
from app.services.rule_engine import CaseSpec


def _enrolments(spec: CaseSpec, record_id: int | None) -> list[dict[str, Any]]:
    return [
        {**e, "record_id": record_id} if e.get("record_id") is None else dict(e)
        for e in spec.enrolments
    ]


def _merge(existing: list[dict], new: list[dict]) -> list[dict]:
    by_id = {e.get("record_id"): e for e in existing}
    for e in new:
        by_id[e.get("record_id")] = e
    return sorted(by_id.values(), key=lambda e: (e.get("admission_date") or "", e["record_id"]))


def _fields(spec: CaseSpec, enrolments: list[dict]) -> dict[str, Any]:
    record_ids = [e["record_id"] for e in enrolments]
    institutions = sorted({e["institution_id"] for e in enrolments})
    return dict(
        subject_ids_json={
            "key": spec.key,
            "record_ids": record_ids,
            "institution_ids": institutions,
        },
        evidence_json={
            "code": spec.code,
            **spec.extra,
            "institutions": len(institutions),
            "enrolments": enrolments,
        },
        explanation_json={
            "summary_en": spec.explanation_en,
            "message_key": f"case.{spec.code.lower()}",
        },
        score=None,
    )


def persist_case_specs(db: Session, specs: list[CaseSpec], record_id: int | None) -> list[int]:
    """Open or merge each case (live path). `record_id` replaces the candidate placeholder."""
    ids = []
    for spec in specs:
        db.execute(select(func.pg_advisory_xact_lock(func.hashtext(spec.key))))
        enrolments = _enrolments(spec, record_id)
        case = db.scalar(
            select(Case)
            .where(Case.status == CaseStatus.OPEN, Case.subject_ids_json["key"].astext == spec.key)
            .execution_options(populate_existing=True)
        )
        if case is not None:
            enrolments = _merge(case.evidence_json.get("enrolments", []), enrolments)
            for name, value in _fields(spec, enrolments).items():
                setattr(case, name, value)
        else:
            case = Case(
                type=spec.type,
                visibility=spec.visibility,
                priority=spec.priority,
                severity=spec.severity,
                status=CaseStatus.OPEN,
                institution_id=None,  # national, cross-institution case
                **_fields(spec, enrolments),
            )
            db.add(case)
        db.flush()
        ids.append(case.id)
    return ids


def bulk_case_rows(specs: list[CaseSpec]) -> list[dict[str, Any]]:
    """Merge specs by key in memory (replay) and return insertable rows."""
    merged: dict[str, tuple[CaseSpec, list[dict]]] = {}
    for spec in specs:
        enrolments = _enrolments(spec, None)
        if spec.key in merged:
            first, existing = merged[spec.key]
            merged[spec.key] = (first, _merge(existing, enrolments))
        else:
            merged[spec.key] = (spec, _merge([], enrolments))
    return [
        dict(
            type=spec.type,
            visibility=spec.visibility,
            priority=spec.priority,
            severity=spec.severity,
            status=CaseStatus.OPEN,
            institution_id=None,
            **_fields(spec, enrolments),
        )
        for spec, enrolments in merged.values()
    ]
