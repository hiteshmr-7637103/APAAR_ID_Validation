"""ML triage service (spec 5.4, 9.1, 9.2, 9.3; FR-14, FR-15, FR-16).

Coordinates ML triage pipelines:
1. Fuzzy duplicate matcher: blocks against stored cross-institution records, predicts
   calibrated match probabilities, filters genuine transfers, and opens DUP_FUZZY cases
   (AICTE_ONLY, priority 1 or 2).
2. Shared-identity ring detector: analyzes contact tokens across institutions, runs
   Louvain community detection, and opens RING cases (AICTE_ONLY, priority 1).
3. Institution anomaly detector: computes peer-normalised institution metrics and
   Isolation Forest anomaly scores with z-score explanations, opening ANOMALY cases
   (AICTE_ONLY).

ML NEVER automatically modifies status, deletes, or auto-links records (NFR-8).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Case,
    CaseStatus,
    CaseType,
    CaseVisibility,
    Institution,
    InstitutionProgramme,
    Programme,
    RecordStatus,
    Severity,
    Student,
    StudentRecord,
    ValidationResult,
)
from app.ml.anomaly import InstitutionMetrics, default_anomaly_detector
from app.ml.dedupe import RecordEntity, default_matcher
from app.ml.rings import ContactRecord, default_ring_detector

logger = logging.getLogger("apaar.services.triage")


def _record_entities(db: Session, record_ids: list[int]) -> list[RecordEntity]:
    """Load lightweight RecordEntity instances from DB for candidate records."""
    if not record_ids:
        return []

    stmt = (
        select(
            StudentRecord.id,
            StudentRecord.institution_id,
            Student.apaar_id,
            Student.name_norm,
            Student.name_phonetic,
            Student.dob,
            Student.gender,
            Programme.canonical_code,
            StudentRecord.admission_date,
            StudentRecord.end_date,
            StudentRecord.contact_token,
            StudentRecord.email_token,
            StudentRecord.address_token,
        )
        .join(Student, Student.id == StudentRecord.student_id)
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
        .where(StudentRecord.id.in_(record_ids))
    )

    entities = []
    for row in db.execute(stmt):
        entities.append(
            RecordEntity(
                id=row[0],
                institution_id=row[1],
                apaar_id=row[2],
                name_norm=row[3],
                name_phonetic=row[4],
                dob=row[5],
                gender=str(row[6]) if row[6] else None,
                programme_code=row[7],
                admission_date=row[8],
                end_date=row[9],
                contact_token=row[10],
                email_token=row[11],
                address_token=row[12],
            )
        )
    return entities


def triage_duplicate_records(db: Session, target_record_ids: list[int]) -> list[int]:
    """Run duplicate triage for specific records against other institutions."""
    if not target_record_ids:
        return []

    new_entities = _record_entities(db, target_record_ids)
    if not new_entities:
        return []

    # Exclude already resolved or rejected records from comparison
    target_inst_ids = {e.institution_id for e in new_entities}

    # Fetch stored active records from other institutions (limit candidate pool size for speed)
    stored_stmt = (
        select(StudentRecord.id)
        .where(
            StudentRecord.institution_id.notin_(target_inst_ids),
            StudentRecord.status.in_([RecordStatus.ACTIVE, RecordStatus.PENDING_APPROVAL]),
        )
        .order_by(StudentRecord.id.desc())
        .limit(10000)
    )
    stored_ids = list(db.scalars(stored_stmt).all())
    stored_entities = _record_entities(db, stored_ids)

    # Find existing open DUP_FUZZY cases to avoid duplicates
    existing_keys = set(
        db.scalars(
            select(Case.subject_ids_json["key"].astext).where(
                Case.type == CaseType.DUP_FUZZY, Case.status == CaseStatus.OPEN
            )
        ).all()
    )

    matches = default_matcher.match_against_stored(new_entities, stored_entities)
    opened_case_ids = []

    for match in matches:
        pair_min = min(match.record_a_id, match.record_b_id)
        pair_max = max(match.record_a_id, match.record_b_id)
        key = f"DUP_FUZZY:{pair_min}:{pair_max}"

        if key in existing_keys:
            continue
        existing_keys.add(key)

        explanation = {
            "summary_en": (
                f"Fuzzy cross-institution duplicate detected with match probability "
                f"{match.probability:.1%}. Reviewer decision required."
            ),
            "probability": match.probability,
            "label": match.evidence["label"],
        }

        inst_ids = [
            match.evidence["record_a"]["institution_id"],
            match.evidence["record_b"]["institution_id"],
        ]

        case = Case(
            type=CaseType.DUP_FUZZY,
            visibility=CaseVisibility.AICTE_ONLY,
            priority=match.priority,
            severity=Severity.WARNING,
            status=CaseStatus.OPEN,
            institution_id=None,  # Cross-institution case: visible only to AICTE
            subject_ids_json={
                "key": key,
                "record_ids": [match.record_a_id, match.record_b_id],
                "institution_ids": inst_ids,
            },
            score=match.probability,
            evidence_json=match.evidence,
            explanation_json=explanation,
        )
        db.add(case)
        db.flush()
        opened_case_ids.append(case.id)

    return opened_case_ids


def triage_rings(db: Session, min_shared: int = 3) -> list[int]:
    """Detect shared-identity rings and open RING cases."""
    stmt = (
        select(
            StudentRecord.id,
            StudentRecord.student_id,
            Student.apaar_id,
            StudentRecord.institution_id,
            Institution.code,
            StudentRecord.contact_token,
            StudentRecord.email_token,
            StudentRecord.address_token,
        )
        .join(Student, Student.id == StudentRecord.student_id)
        .join(Institution, Institution.id == StudentRecord.institution_id)
        .where(StudentRecord.status.in_([RecordStatus.ACTIVE, RecordStatus.PENDING_APPROVAL]))
        .where(
            (StudentRecord.contact_token.isnot(None))
            | (StudentRecord.email_token.isnot(None))
            | (StudentRecord.address_token.isnot(None))
        )
        .limit(25000)
    )

    contact_records = []
    for row in db.execute(stmt):
        contact_records.append(
            ContactRecord(
                record_id=row[0],
                student_id=row[1],
                apaar_id=row[2],
                institution_id=row[3],
                institution_code=row[4],
                contact_token=row[5],
                email_token=row[6],
                address_token=row[7],
            )
        )

    rings = default_ring_detector.detect_rings(contact_records)
    opened_ids = []

    existing_open = set(
        db.scalars(
            select(Case.subject_ids_json["key"].astext).where(
                Case.type == CaseType.RING, Case.status == CaseStatus.OPEN
            )
        ).all()
    )

    for ring in rings:
        key = f"RING:{ring.masked_token}"
        if key in existing_open:
            continue
        existing_open.add(key)

        explanation = {
            "summary_en": (
                f"Shared contact token ({ring.token_type}) shared across {ring.size} records "
                f"and {ring.institutions_involved_count} institutions."
            ),
            "size": ring.size,
            "institutions_involved": ring.institution_codes,
        }

        case = Case(
            type=CaseType.RING,
            visibility=CaseVisibility.AICTE_ONLY,
            priority=1,
            severity=Severity.WARNING,
            status=CaseStatus.OPEN,
            institution_id=None,
            subject_ids_json={
                "key": key,
                "record_ids": ring.record_ids,
                "student_ids": ring.student_ids,
                "institution_ids": ring.institution_ids,
            },
            score=float(min(ring.size / 10.0, 1.0)),
            evidence_json=ring.evidence,
            explanation_json=explanation,
        )
        db.add(case)
        db.flush()
        opened_ids.append(case.id)

    return opened_ids


def triage_anomalies(db: Session, academic_year: str | None = None) -> list[int]:
    """Compute institution metrics and open ANOMALY cases for outlier institutions."""
    year = academic_year or settings.current_academic_year

    # Aggregate metrics per institution
    institutions = db.scalars(select(Institution)).all()
    metrics_list: list[InstitutionMetrics] = []

    for inst in institutions:
        prog_rows = db.scalars(
            select(InstitutionProgramme).where(
                InstitutionProgramme.institution_id == inst.id,
                InstitutionProgramme.academic_year == year,
            )
        ).all()
        if not prog_rows:
            continue

        approved_intake = sum(p.approved_intake for p in prog_rows)
        declared_enrolment = sum(p.declared_enrolment or 0 for p in prog_rows)

        records = db.scalars(
            select(StudentRecord)
            .join(
                InstitutionProgramme,
                InstitutionProgramme.id == StudentRecord.institution_programme_id,
            )
            .where(
                StudentRecord.institution_id == inst.id,
                InstitutionProgramme.academic_year == year,
                StudentRecord.status != RecordStatus.REJECTED,
            )
        ).all()

        tot = len(records)
        active = sum(1 for r in records if r.status == RecordStatus.ACTIVE)
        # Late admissions: in last 7 days of admission window (Sept 24 - Sept 30)
        late = sum(
            1
            for r in records
            if r.admission_date and r.admission_date.month == 9 and r.admission_date.day >= 24
        )
        supernum = sum(
            1
            for r in records
            if r.ledger_key
            in (
                "SUPERNUMERARY_FN_OCI_PIO_CIWGC",
                "SUPERNUMERARY_TFW",
                "NRI",
            )
        )
        lateral = sum(1 for r in records if r.ledger_key == "LATERAL")

        cat_counts = defaultdict(int)
        for r in records:
            cat_counts[str(r.admission_category)] += 1

        # Warnings count from validation_results
        rec_ids = [r.id for r in records]
        warn_count = 0
        mismatch_count = 0
        if rec_ids:
            warn_count = (
                db.scalar(
                    select(func.count(ValidationResult.id)).where(
                        ValidationResult.student_record_id.in_(rec_ids),
                        ValidationResult.severity == Severity.WARNING,
                    )
                )
                or 0
            )
            mismatch_count = (
                db.scalar(
                    select(func.count(ValidationResult.id)).where(
                        ValidationResult.student_record_id.in_(rec_ids),
                        ValidationResult.code == "APAAR_MISMATCH",
                    )
                )
                or 0
            )

        metrics_list.append(
            InstitutionMetrics(
                institution_id=inst.id,
                institution_code=inst.code,
                state=inst.state,
                academic_year=year,
                approved_intake=approved_intake,
                declared_enrolment=declared_enrolment if declared_enrolment > 0 else None,
                total_records=tot,
                active_records=active,
                late_admissions=late,
                supernumerary_count=supernum,
                lateral_count=lateral,
                warning_count=warn_count,
                apaar_mismatch_count=mismatch_count,
                prior_year_records=None,
                category_counts=dict(cat_counts),
            )
        )

    if not metrics_list:
        return []

    scores = default_anomaly_detector.analyze_institutions(metrics_list)
    opened_ids = []

    existing_open = set(
        db.scalars(
            select(Case.subject_ids_json["key"].astext).where(
                Case.type == CaseType.ANOMALY, Case.status == CaseStatus.OPEN
            )
        ).all()
    )

    for sc in scores:
        if not sc.is_anomalous:
            continue

        key = f"ANOMALY:{sc.institution_id}:{year}"
        if key in existing_open:
            continue
        existing_open.add(key)

        top_feat_str = ", ".join(
            f"{tf['feature']} (z={tf['z_score']})" for tf in sc.top_features[:2]
        )
        explanation = {
            "summary_en": (
                f"Institution flagged as statistical outlier (score {sc.anomaly_score:.2f}, "
                f"rank {sc.rank}). Top deviations: {top_feat_str}."
            ),
            "anomaly_score": sc.anomaly_score,
            "rank": sc.rank,
            "top_features": sc.top_features,
        }

        case = Case(
            type=CaseType.ANOMALY,
            visibility=CaseVisibility.AICTE_ONLY,
            priority=2,
            severity=Severity.WARNING,
            status=CaseStatus.OPEN,
            institution_id=sc.institution_id,
            subject_ids_json={
                "key": key,
                "institution_id": sc.institution_id,
                "academic_year": year,
            },
            score=sc.anomaly_score,
            evidence_json={
                "institution_code": sc.institution_code,
                "raw_features": sc.raw_features,
                "top_features": sc.top_features,
            },
            explanation_json=explanation,
        )
        db.add(case)
        db.flush()
        opened_ids.append(case.id)

    return opened_ids


def triage_submission(db: Session, record_ids: list[int]) -> dict[str, Any]:
    """Entry point called after submit: runs duplicate and ring triage."""
    dup_cases = triage_duplicate_records(db, record_ids)
    ring_cases = triage_rings(db)
    return {
        "duplicate_cases_opened": len(dup_cases),
        "ring_cases_opened": len(ring_cases),
    }
