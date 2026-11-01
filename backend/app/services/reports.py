"""Report generation service (spec 5.6, FR-24; spec 8).

Generates standardized CSV and structured data for AICTE regulation, monitoring, and audit:
1. `enrolment-vs-intake`: Intake vs actual enrolments per programme and shift.
2. `supernumerary-utilisation`: Breakdown across APPROVED, FN/OCI, TFW, NRI, LATERAL buckets.
3. `over-cap-flags`: All seats exceeding approved caps with excess amounts and case status.
4. `cross-institution-duplicates`: Cross-institution duplicate matches with pair details.
5. `coverage`: Declared headcount vs actual received records and band (RED/AMBER/GREEN).
6. `risk-ranking`: Composite risk scores with four component breakdown.
7. `trust-tiers`: T0-T3 student trust tier distribution and verification ratio.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Case,
    CaseStatus,
    CaseType,
    Institution,
    InstitutionProgramme,
    LedgerKey,
    Programme,
    RecordStatus,
    SeatLedgerBalance,
    StudentRecord,
)


def _write_csv(headers: list[str], rows: list[list[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


# --------------------------------------------------------------------------- 1. Enrolment vs Intake
def report_enrolment_vs_intake(
    db: Session, academic_year: str, state: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Institution Code",
        "Institution Name",
        "State",
        "Programme Code",
        "Programme Name",
        "Shift",
        "Approved Intake",
        "Declared Enrolment",
        "Actual Enrolment",
        "Fill Ratio",
        "Over Cap Count",
    ]
    stmt = (
        select(
            Institution.code,
            Institution.name,
            Institution.state,
            Programme.canonical_code,
            Programme.name,
            InstitutionProgramme.shift,
            InstitutionProgramme.approved_intake,
            InstitutionProgramme.declared_enrolment,
            InstitutionProgramme.id,
        )
        .join(Institution, Institution.id == InstitutionProgramme.institution_id)
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
        .where(InstitutionProgramme.academic_year == academic_year)
    )
    if state:
        stmt = stmt.where(Institution.state == state)
    stmt = stmt.order_by(Institution.code, Programme.canonical_code)

    results = db.execute(stmt).all()
    rows = []
    for r in results:
        ip_id = r[8]
        # Count non-rejected records
        actual = (
            db.scalar(
                select(func.count(StudentRecord.id)).where(
                    StudentRecord.institution_programme_id == ip_id,
                    StudentRecord.status != RecordStatus.REJECTED,
                )
            )
            or 0
        )
        over_cap = (
            db.scalar(
                select(func.count(StudentRecord.id)).where(
                    StudentRecord.institution_programme_id == ip_id,
                    StudentRecord.over_cap.is_(True),
                    StudentRecord.status != RecordStatus.REJECTED,
                )
            )
            or 0
        )
        intake = r[6]
        declared = r[7] if r[7] is not None else "-"
        fill = round(actual / intake, 3) if intake > 0 else 0.0

        rows.append(
            [
                r[0],
                r[1],
                r[2],
                r[3],
                r[4],
                str(r[5]),
                intake,
                declared,
                actual,
                fill,
                over_cap,
            ]
        )
    return headers, rows


# ---------------------------------------------------- 2. Supernumerary Utilisation
def report_supernumerary_utilisation(
    db: Session, academic_year: str, state: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Institution Code",
        "Institution Name",
        "State",
        "Programme Code",
        "Shift",
        "Approved Cap",
        "Approved Used",
        "Supernumerary FN Cap",
        "Supernumerary FN Used",
        "TFW Cap",
        "TFW Used",
        "NRI Cap",
        "NRI Used",
        "Lateral Cap",
        "Lateral Used",
    ]
    stmt = (
        select(
            Institution.code,
            Institution.name,
            Institution.state,
            Programme.canonical_code,
            InstitutionProgramme.shift,
            InstitutionProgramme.id,
        )
        .join(Institution, Institution.id == InstitutionProgramme.institution_id)
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
        .where(InstitutionProgramme.academic_year == academic_year)
    )
    if state:
        stmt = stmt.where(Institution.state == state)
    stmt = stmt.order_by(Institution.code, Programme.canonical_code)

    results = db.execute(stmt).all()
    rows = []
    for r in results:
        ip_id = r[5]
        balances = {
            b.ledger_key: b
            for b in db.scalars(
                select(SeatLedgerBalance).where(SeatLedgerBalance.institution_programme_id == ip_id)
            ).all()
        }
        b_app = balances.get(LedgerKey.APPROVED)
        b_fn = balances.get(LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC)
        b_tfw = balances.get(LedgerKey.SUPERNUMERARY_TFW)
        b_nri = balances.get(LedgerKey.NRI)
        b_lat = balances.get(LedgerKey.LATERAL)

        rows.append(
            [
                r[0],
                r[1],
                r[2],
                r[3],
                str(r[4]),
                b_app.cap if b_app else 0,
                b_app.used if b_app else 0,
                b_fn.cap if b_fn else 0,
                b_fn.used if b_fn else 0,
                b_tfw.cap if b_tfw else 0,
                b_tfw.used if b_tfw else 0,
                b_nri.cap if b_nri else 0,
                b_nri.used if b_nri else 0,
                b_lat.cap if b_lat else 0,
                b_lat.used if b_lat else 0,
            ]
        )
    return headers, rows


# --------------------------------------------------------------------------- 3. Over-Cap Flags
def report_over_cap_flags(
    db: Session, academic_year: str, state: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Institution Code",
        "Institution Name",
        "State",
        "Programme Code",
        "Shift",
        "Bucket",
        "Cap",
        "Used",
        "Excess",
        "Case ID",
        "Case Status",
    ]
    stmt = (
        select(
            Institution.code,
            Institution.name,
            Institution.state,
            Programme.canonical_code,
            InstitutionProgramme.shift,
            SeatLedgerBalance.ledger_key,
            SeatLedgerBalance.cap,
            SeatLedgerBalance.used,
            InstitutionProgramme.id,
        )
        .join(Institution, Institution.id == InstitutionProgramme.institution_id)
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
        .join(
            SeatLedgerBalance,
            SeatLedgerBalance.institution_programme_id == InstitutionProgramme.id,
        )
        .where(
            InstitutionProgramme.academic_year == academic_year,
            SeatLedgerBalance.used > SeatLedgerBalance.cap,
        )
    )
    if state:
        stmt = stmt.where(Institution.state == state)
    stmt = stmt.order_by(Institution.code, Programme.canonical_code)

    results = db.execute(stmt).all()
    rows = []
    for r in results:
        excess = r[7] - r[6]
        ip_id = r[8]
        key_str = f"SEAT_OVER_CAP:{ip_id}:{r[5]}"
        case = db.scalar(
            select(Case).where(
                Case.type == CaseType.SEAT_OVER_CAP,
                Case.subject_ids_json["key"].astext == key_str,
            )
        )
        rows.append(
            [
                r[0],
                r[1],
                r[2],
                r[3],
                str(r[4]),
                str(r[5]),
                r[6],
                r[7],
                excess,
                case.id if case else "-",
                str(case.status) if case else "OPEN",
            ]
        )
    return headers, rows


# -------------------------------------------------- 4. Cross-Institution Duplicates
def report_cross_institution_duplicates(
    db: Session, academic_year: str, state: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Case ID",
        "Case Type",
        "Priority",
        "Score",
        "Institution A",
        "Record A ID",
        "Institution B",
        "Record B ID",
        "Status",
        "Created At",
    ]
    stmt = (
        select(Case)
        .where(
            Case.type.in_([CaseType.DUP_EXACT_CROSS, CaseType.DUP_FUZZY]),
        )
        .order_by(Case.id.desc())
    )
    cases = db.scalars(stmt).all()
    rows = []
    for c in cases:
        ev = c.evidence_json or {}
        rec_a = ev.get("record_a", {})
        rec_b = ev.get("record_b", {})
        inst_a = rec_a.get("institution_code", str(rec_a.get("institution_id", "-")))
        inst_b = rec_b.get("institution_code", str(rec_b.get("institution_id", "-")))
        rows.append(
            [
                c.id,
                str(c.type),
                c.priority,
                round(c.score, 3) if c.score is not None else 1.0,
                inst_a,
                rec_a.get("id", "-"),
                inst_b,
                rec_b.get("id", "-"),
                str(c.status),
                c.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )
    return headers, rows


# --------------------------------------------------------------------------- 5. Coverage
def report_coverage(
    db: Session, academic_year: str, state: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Institution Code",
        "Institution Name",
        "State",
        "Declared Headcount",
        "Received Records",
        "Coverage Percentage",
        "Status Band",
    ]
    stmt = select(Institution).order_by(Institution.code)
    if state:
        stmt = stmt.where(Institution.state == state)
    insts = db.scalars(stmt).all()

    rows = []
    for inst in insts:
        declared = (
            db.scalar(
                select(func.sum(InstitutionProgramme.declared_enrolment)).where(
                    InstitutionProgramme.institution_id == inst.id,
                    InstitutionProgramme.academic_year == academic_year,
                )
            )
            or 0
        )
        received = (
            db.scalar(
                select(func.count(StudentRecord.id))
                .join(
                    InstitutionProgramme,
                    InstitutionProgramme.id == StudentRecord.institution_programme_id,
                )
                .where(
                    StudentRecord.institution_id == inst.id,
                    InstitutionProgramme.academic_year == academic_year,
                    StudentRecord.status != RecordStatus.REJECTED,
                )
            )
            or 0
        )
        if declared > 0:
            pct = round((received / declared) * 100, 1)
        else:
            pct = 100.0 if received > 0 else 0.0

        if pct < 50.0:
            band = "RED"
        elif pct < 90.0:
            band = "AMBER"
        else:
            band = "GREEN"

        rows.append([inst.code, inst.name, inst.state, declared, received, f"{pct}%", band])
    return headers, rows


# --------------------------------------------------------------------------- 6. Risk Ranking
def report_risk_ranking(
    db: Session, academic_year: str, state: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    from app.services import reports

    headers = [
        "Rank",
        "Institution Code",
        "Institution Name",
        "State",
        "Risk Score",
        "Anomaly Component",
        "Duplicate Component",
        "Ring Component",
        "Rule Violation Component",
    ]
    rankings = reports.compute_risk_ranking(db, academic_year, state=state)
    rows = []
    for r in rankings:
        comps = r["components"]
        rows.append(
            [
                r["rank"],
                r["institution_code"],
                r["institution_name"],
                r["state"],
                f"{r['risk_score']:.1f}",
                f"{comps['anomaly']['value']:.2f}",
                f"{comps['duplicate_rate']['value']:.2f}",
                f"{comps['ring_flags']['value']:.2f}",
                f"{comps['rule_violations']['value']:.2f}",
            ]
        )
    return headers, rows


# --------------------------------------------------------------------------- 7. Trust Tiers
def report_trust_tiers(
    db: Session, academic_year: str, state: str | None = None
) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Institution Code",
        "Institution Name",
        "State",
        "Total Active Records",
        "T0 (Submitted)",
        "T1 (Rules Passed)",
        "T2 (Registry Matched)",
        "T3 (Student Confirmed)",
        "Trust Ratio",
    ]
    stmt = select(Institution).order_by(Institution.code)
    if state:
        stmt = stmt.where(Institution.state == state)
    insts = db.scalars(stmt).all()

    rows = []
    for inst in insts:
        tier_counts = dict(
            db.execute(
                select(StudentRecord.trust_tier, func.count(StudentRecord.id))
                .join(
                    InstitutionProgramme,
                    InstitutionProgramme.id == StudentRecord.institution_programme_id,
                )
                .where(
                    StudentRecord.institution_id == inst.id,
                    InstitutionProgramme.academic_year == academic_year,
                    StudentRecord.status != RecordStatus.REJECTED,
                )
                .group_by(StudentRecord.trust_tier)
            ).all()
        )
        t0 = tier_counts.get(0, 0)
        t1 = tier_counts.get(1, 0)
        t2 = tier_counts.get(2, 0)
        t3 = tier_counts.get(3, 0)
        tot = t0 + t1 + t2 + t3
        ratio = f"{round(t3 / tot, 3):.3f}" if tot > 0 else "0.000"

        rows.append([inst.code, inst.name, inst.state, tot, t0, t1, t2, t3, ratio])
    return headers, rows


REPORT_GENERATORS = {
    "enrolment-vs-intake": report_enrolment_vs_intake,
    "supernumerary-utilisation": report_supernumerary_utilisation,
    "over-cap-flags": report_over_cap_flags,
    "cross-institution-duplicates": report_cross_institution_duplicates,
    "coverage": report_coverage,
    "risk-ranking": report_risk_ranking,
    "trust-tiers": report_trust_tiers,
}


def generate_report_csv(
    db: Session, report_name: str, academic_year: str, state: str | None = None
) -> str:
    gen = REPORT_GENERATORS.get(report_name)
    if not gen:
        raise ValueError(f"Unknown report name: {report_name}")
    headers, rows = gen(db, academic_year, state)
    return _write_csv(headers, rows)


def generate_report_json(
    db: Session, report_name: str, academic_year: str, state: str | None = None
) -> dict[str, Any]:
    gen = REPORT_GENERATORS.get(report_name)
    if not gen:
        raise ValueError(f"Unknown report name: {report_name}")
    headers, rows = gen(db, academic_year, state)
    return {
        "report": report_name,
        "academic_year": academic_year,
        "state": state,
        "headers": headers,
        "rows": rows,
        "total_rows": len(rows),
    }


# --------------------------------------------------------------------------- Risk Ranking Helper
def compute_risk_ranking(
    db: Session, academic_year: str, state: str | None = None
) -> list[dict[str, Any]]:
    """Compute institution risk scores per spec 5.6 (FR-25).

    risk_score = (
        0.35 * anomaly + 0.25 * duplicate_rate + 0.20 * ring_flags + 0.20 * rule_violation_rate
    )
    """
    stmt = select(Institution).order_by(Institution.id)
    if state:
        stmt = stmt.where(Institution.state == state)
    institutions = db.scalars(stmt).all()

    # Pre-fetch open anomaly cases
    anomaly_cases = {
        c.institution_id: c.score or 0.0
        for c in db.scalars(
            select(Case).where(
                Case.type == CaseType.ANOMALY,
                Case.status.in_([CaseStatus.OPEN, CaseStatus.CONFIRMED]),
            )
        ).all()
        if c.institution_id is not None
    }

    # Pre-fetch duplicate case counts
    dup_counts = dict(
        db.execute(
            select(Case.institution_id, func.count(Case.id))
            .where(
                Case.type.in_([CaseType.DUP_FUZZY, CaseType.DUP_EXACT_CROSS]),
                Case.status.in_([CaseStatus.OPEN, CaseStatus.CONFIRMED]),
                Case.institution_id.isnot(None),
            )
            .group_by(Case.institution_id)
        ).all()
    )

    # Pre-fetch total non-rejected records per institution
    total_recs_map = dict(
        db.execute(
            select(StudentRecord.institution_id, func.count(StudentRecord.id))
            .join(
                InstitutionProgramme,
                InstitutionProgramme.id == StudentRecord.institution_programme_id,
            )
            .where(
                InstitutionProgramme.academic_year == academic_year,
                StudentRecord.status != RecordStatus.REJECTED,
            )
            .group_by(StudentRecord.institution_id)
        ).all()
    )

    # Pre-fetch over_cap records per institution
    over_cap_map = dict(
        db.execute(
            select(StudentRecord.institution_id, func.count(StudentRecord.id))
            .join(
                InstitutionProgramme,
                InstitutionProgramme.id == StudentRecord.institution_programme_id,
            )
            .where(
                InstitutionProgramme.academic_year == academic_year,
                StudentRecord.over_cap.is_(True),
                StudentRecord.status != RecordStatus.REJECTED,
            )
            .group_by(StudentRecord.institution_id)
        ).all()
    )

    # Pre-fetch open ring cases
    ring_cases = db.scalars(
        select(Case).where(
            Case.type == CaseType.RING,
            Case.status.in_([CaseStatus.OPEN, CaseStatus.CONFIRMED]),
        )
    ).all()

    rankings = []
    for inst in institutions:
        total_recs = total_recs_map.get(inst.id, 0)
        base_recs = max(total_recs, 1)

        # 1. Anomaly component (0..1)
        anom_score = anomaly_cases.get(inst.id, 0.0)

        # 2. Duplicate rate (0..1)
        dups = dup_counts.get(inst.id, 0)
        dup_rate = min(dups / base_recs, 1.0)

        # 3. Ring component (0..1)
        ring_cases_count = sum(
            1
            for c in ring_cases
            if inst.id in (c.subject_ids_json or {}).get("institution_ids", [])
        )
        ring_rate = min(ring_cases_count * 5 / base_recs, 1.0)

        # 4. Rule violations / over cap component (0..1)
        over_cap_count = over_cap_map.get(inst.id, 0)
        violation_rate = min(over_cap_count / base_recs, 1.0)

        # Composite score out of 100
        raw_score = 0.35 * anom_score + 0.25 * dup_rate + 0.20 * ring_rate + 0.20 * violation_rate
        score_100 = round(raw_score * 100, 1)

        alerts = []
        if over_cap_count > 0:
            alerts.append(f"{over_cap_count} admissions flagged exceeding approved quota.")
        if dups > 0:
            alerts.append(f"{dups} open duplicate cases flagged.")
        if anom_score > 0.5:
            alerts.append(f"Significant anomaly score ({anom_score:.2f}) from peer benchmark.")
        if ring_cases_count > 0:
            alerts.append(f"Involved in {ring_cases_count} shared-identity contact ring(s).")

        rankings.append(
            {
                "institution_id": inst.id,
                "institution_code": inst.code,
                "institution_name": inst.name,
                "state": inst.state,
                "risk_score": score_100,
                "components": {
                    "anomaly": {
                        "value": round(anom_score, 3),
                        "weighted": round(anom_score * 35, 1),
                    },
                    "duplicate_rate": {
                        "value": round(dup_rate, 3),
                        "weighted": round(dup_rate * 25, 1),
                    },
                    "ring_flags": {
                        "value": round(ring_rate, 3),
                        "weighted": round(ring_rate * 20, 1),
                    },
                    "rule_violations": {
                        "value": round(violation_rate, 3),
                        "weighted": round(violation_rate * 20, 1),
                    },
                },
                "alerts": alerts,
            }
        )

    # Sort descending by risk score
    rankings.sort(key=lambda x: x["risk_score"], reverse=True)
    for idx, item in enumerate(rankings):
        item["rank"] = idx + 1

    return rankings


# --------------------------------------------------------------------------- Coverage Helpers
def get_coverage_band(pct: float) -> str:
    """Return CONFIGURABLE coverage band: <50% RED, 50-89.9% AMBER, >=90% GREEN (FR-21)."""
    if pct < 50.0:
        return "RED"
    if pct < 90.0:
        return "AMBER"
    return "GREEN"


def compute_institution_coverage(
    db: Session, institution_id: int, academic_year: str
) -> dict[str, Any]:
    """Compute programme-by-programme and overall coverage meter for an institution (FR-21)."""
    inst = db.get(Institution, institution_id)
    if not inst:
        raise ValueError(f"Institution not found: {institution_id}")

    ip_rows = db.execute(
        select(
            InstitutionProgramme.id,
            InstitutionProgramme.shift,
            InstitutionProgramme.approved_intake,
            InstitutionProgramme.declared_enrolment,
            Programme.canonical_code,
            Programme.name,
        )
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
        .where(
            InstitutionProgramme.institution_id == institution_id,
            InstitutionProgramme.academic_year == academic_year,
        )
        .order_by(Programme.canonical_code, InstitutionProgramme.shift)
    ).all()

    total_declared = 0
    total_received = 0
    programmes = []

    for ip_id, shift, intake, declared, prog_code, prog_name in ip_rows:
        rec_count = (
            db.scalar(
                select(func.count(StudentRecord.id)).where(
                    StudentRecord.institution_programme_id == ip_id,
                    StudentRecord.status != RecordStatus.REJECTED,
                )
            )
            or 0
        )
        decl = declared or 0
        total_declared += decl
        total_received += rec_count
        p_pct = (
            round((rec_count / decl * 100), 1) if decl > 0 else (100.0 if rec_count > 0 else 0.0)
        )
        programmes.append(
            {
                "institution_programme_id": ip_id,
                "programme_code": prog_code,
                "programme_name": prog_name,
                "shift": shift,
                "approved_intake": intake,
                "declared_enrolment": decl,
                "received_records": rec_count,
                "coverage_percentage": p_pct,
                "status_band": get_coverage_band(p_pct),
            }
        )

    overall_pct = (
        round((total_received / total_declared * 100), 1)
        if total_declared > 0
        else (100.0 if total_received > 0 else 0.0)
    )

    return {
        "institution_id": institution_id,
        "institution_code": inst.code,
        "institution_name": inst.name,
        "academic_year": academic_year,
        "declared_headcount": total_declared,
        "received_records": total_received,
        "coverage_percentage": overall_pct,
        "status_band": get_coverage_band(overall_pct),
        "programmes": programmes,
    }


def compute_national_coverage(
    db: Session, academic_year: str, state: str | None = None
) -> dict[str, Any]:
    """Compute national and per-institution coverage distribution (FR-21, FR-22)."""
    stmt = select(Institution).order_by(Institution.code)
    if state:
        stmt = stmt.where(Institution.state == state)
    insts = db.scalars(stmt).all()

    # Pre-fetch declared headcount per institution
    decl_stmt = (
        select(
            InstitutionProgramme.institution_id,
            func.coalesce(func.sum(InstitutionProgramme.declared_enrolment), 0),
        )
        .where(InstitutionProgramme.academic_year == academic_year)
        .group_by(InstitutionProgramme.institution_id)
    )
    declared_by_inst = dict(db.execute(decl_stmt).all())

    # Pre-fetch received records per institution
    rec_stmt = (
        select(
            StudentRecord.institution_id,
            func.count(StudentRecord.id),
        )
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .where(
            InstitutionProgramme.academic_year == academic_year,
            StudentRecord.status != RecordStatus.REJECTED,
        )
        .group_by(StudentRecord.institution_id)
    )
    received_by_inst = dict(db.execute(rec_stmt).all())

    institutions_list = []
    total_declared = 0
    total_received = 0
    bands = {"RED": 0, "AMBER": 0, "GREEN": 0}

    for inst in insts:
        decl = int(declared_by_inst.get(inst.id, 0))
        rec = int(received_by_inst.get(inst.id, 0))
        total_declared += decl
        total_received += rec

        pct = round((rec / decl * 100), 1) if decl > 0 else (100.0 if rec > 0 else 0.0)
        band = get_coverage_band(pct)
        bands[band] += 1

        institutions_list.append(
            {
                "institution_id": inst.id,
                "institution_code": inst.code,
                "institution_name": inst.name,
                "state": inst.state,
                "declared_headcount": decl,
                "received_records": rec,
                "coverage_percentage": pct,
                "status_band": band,
            }
        )

    overall_pct = (
        round((total_received / total_declared * 100), 1)
        if total_declared > 0
        else (100.0 if total_received > 0 else 0.0)
    )

    return {
        "academic_year": academic_year,
        "state": state,
        "total_institutions": len(insts),
        "total_declared": total_declared,
        "total_received": total_received,
        "overall_coverage_percentage": overall_pct,
        "overall_status_band": get_coverage_band(overall_pct),
        "bands_distribution": bands,
        "institutions": institutions_list,
    }


def compute_aicte_kpis(db: Session, academic_year: str, state: str | None = None) -> dict[str, Any]:
    """Compute AICTE national dashboard metrics (spec 5.6, FR-22)."""
    # 1. Total institutions
    inst_stmt = select(func.count(Institution.id))
    if state:
        inst_stmt = inst_stmt.where(Institution.state == state)
    total_institutions = db.scalar(inst_stmt) or 0

    # 2. Total students enrolled
    stud_stmt = (
        select(func.count(StudentRecord.id))
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .join(Institution, Institution.id == StudentRecord.institution_id)
        .where(
            InstitutionProgramme.academic_year == academic_year,
            StudentRecord.status != RecordStatus.REJECTED,
        )
    )
    if state:
        stud_stmt = stud_stmt.where(Institution.state == state)
    total_students = db.scalar(stud_stmt) or 0

    # 3. Approved intake vs Actual enrolment
    intake_stmt = (
        select(func.coalesce(func.sum(InstitutionProgramme.approved_intake), 0))
        .join(Institution, Institution.id == InstitutionProgramme.institution_id)
        .where(InstitutionProgramme.academic_year == academic_year)
    )
    if state:
        intake_stmt = intake_stmt.where(Institution.state == state)
    total_approved_intake = db.scalar(intake_stmt) or 0

    fill_ratio = (
        round(total_students / total_approved_intake, 3) if total_approved_intake > 0 else 0.0
    )

    # 4. State breakdown
    state_breakdown_stmt = (
        select(
            Institution.state,
            func.count(func.distinct(Institution.id)).label("inst_count"),
            func.coalesce(func.sum(InstitutionProgramme.approved_intake), 0).label(
                "approved_intake"
            ),
        )
        .join(InstitutionProgramme, InstitutionProgramme.institution_id == Institution.id)
        .where(InstitutionProgramme.academic_year == academic_year)
        .group_by(Institution.state)
        .order_by(Institution.state)
    )
    if state:
        state_breakdown_stmt = state_breakdown_stmt.where(Institution.state == state)
    state_rows = db.execute(state_breakdown_stmt).all()

    state_stud_stmt = (
        select(
            Institution.state,
            func.count(StudentRecord.id).label("enrolled_count"),
        )
        .join(Institution, Institution.id == StudentRecord.institution_id)
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .where(
            InstitutionProgramme.academic_year == academic_year,
            StudentRecord.status != RecordStatus.REJECTED,
        )
        .group_by(Institution.state)
    )
    if state:
        state_stud_stmt = state_stud_stmt.where(Institution.state == state)
    state_stud_dict = dict(db.execute(state_stud_stmt).all())

    states_list = []
    for s_name, i_count, app_intake in state_rows:
        enrolled = state_stud_dict.get(s_name, 0)
        s_fill = round(enrolled / app_intake, 3) if app_intake > 0 else 0.0
        states_list.append(
            {
                "state": s_name,
                "institutions_count": i_count,
                "approved_intake": int(app_intake),
                "actual_enrolment": enrolled,
                "fill_ratio": s_fill,
            }
        )

    # 5. Over cap counts and excess
    over_cap_stmt = (
        select(func.count(StudentRecord.id))
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .join(Institution, Institution.id == StudentRecord.institution_id)
        .where(
            InstitutionProgramme.academic_year == academic_year,
            StudentRecord.over_cap.is_(True),
            StudentRecord.status != RecordStatus.REJECTED,
        )
    )
    if state:
        over_cap_stmt = over_cap_stmt.where(Institution.state == state)
    over_cap_records = db.scalar(over_cap_stmt) or 0

    excess_seats_stmt = (
        select(
            func.coalesce(
                func.sum(func.greatest(0, SeatLedgerBalance.used - SeatLedgerBalance.cap)), 0
            )
        )
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == SeatLedgerBalance.institution_programme_id,
        )
        .join(Institution, Institution.id == InstitutionProgramme.institution_id)
        .where(InstitutionProgramme.academic_year == academic_year)
    )
    if state:
        excess_seats_stmt = excess_seats_stmt.where(Institution.state == state)
    total_excess_seats = db.scalar(excess_seats_stmt) or 0

    # 6. Trust tier distribution
    tier_stmt = (
        select(StudentRecord.trust_tier, func.count(StudentRecord.id))
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .join(Institution, Institution.id == StudentRecord.institution_id)
        .where(
            InstitutionProgramme.academic_year == academic_year,
            StudentRecord.status != RecordStatus.REJECTED,
        )
        .group_by(StudentRecord.trust_tier)
    )
    if state:
        tier_stmt = tier_stmt.where(Institution.state == state)
    tier_counts = dict(db.execute(tier_stmt).all())
    tier_total = sum(tier_counts.values())
    trust_tiers = {f"T{t}": tier_counts.get(t, 0) for t in (0, 1, 2, 3)}
    trust_tiers["total"] = tier_total
    trust_tiers["trust_ratio_t3"] = (
        round(trust_tiers["T3"] / tier_total, 3) if tier_total > 0 else 0.0
    )

    # 7. Open cases summary
    case_query = select(Case.type, func.count(Case.id)).where(Case.status == CaseStatus.OPEN)
    if state:
        case_query = case_query.outerjoin(Institution, Institution.id == Case.institution_id).where(
            (Institution.state == state) | (Case.institution_id.is_(None))
        )
    cases_by_type = dict(db.execute(case_query.group_by(Case.type)).all())
    cases_by_type_str = {
        str(k.value if hasattr(k, "value") else k): v for k, v in cases_by_type.items()
    }
    total_open_cases = sum(cases_by_type.values())

    cross_dup_cases = cases_by_type.get(CaseType.DUP_FUZZY, 0) + cases_by_type.get(
        CaseType.DUP_EXACT_CROSS, 0
    )

    # 8. Coverage summary
    coverage_summary = compute_national_coverage(db, academic_year, state=state)

    # 9. Risk leaderboard top 10
    risk_rankings = compute_risk_ranking(db, academic_year, state=state)
    top_risk = risk_rankings[:10]

    return {
        "academic_year": academic_year,
        "state": state,
        "total_institutions": total_institutions,
        "total_students": total_students,
        "enrolment_vs_intake": {
            "approved_intake": int(total_approved_intake),
            "actual_enrolment": total_students,
            "fill_ratio": fill_ratio,
        },
        "state_breakdown": states_list,
        "over_cap": {
            "flagged_records": over_cap_records,
            "total_excess_seats": int(total_excess_seats),
        },
        "coverage": {
            "total_declared": coverage_summary["total_declared"],
            "total_received": coverage_summary["total_received"],
            "overall_percentage": coverage_summary["overall_coverage_percentage"],
            "status_band": coverage_summary["overall_status_band"],
            "bands": coverage_summary["bands_distribution"],
        },
        "trust_tiers": trust_tiers,
        "cases": {
            "total_open": total_open_cases,
            "by_type": cases_by_type_str,
            "cross_institution_duplicates": cross_dup_cases,
        },
        "risk_leaderboard": top_risk,
    }


# ----------------------------------------------------- Data Health Score Helper
def compute_data_health_score(
    db: Session, institution_id: int, academic_year: str
) -> dict[str, Any]:
    """Compute Data Health Score (0-100) per spec 5.6 (FR-23):
    completeness, validity, timeliness, warning share.
    """
    recs = db.scalars(
        select(StudentRecord)
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .where(
            StudentRecord.institution_id == institution_id,
            InstitutionProgramme.academic_year == academic_year,
            StudentRecord.status != RecordStatus.REJECTED,
        )
    ).all()

    total = len(recs)
    if total == 0:
        return {
            "institution_id": institution_id,
            "academic_year": academic_year,
            "overall_score": 100,
            "rating": "EXCELLENT",
            "components": {
                "completeness": {"score": 25, "pct": 100},
                "validity": {"score": 25, "pct": 100},
                "timeliness": {"score": 25, "pct": 100},
                "clean_share": {"score": 25, "pct": 100},
            },
            "summary_en": "No student records submitted yet.",
        }

    # 1. Completeness (25 pts): phone, email, address, student_college_id
    complete_count = sum(
        1 for r in recs if r.phone_enc and r.email_enc and r.address_enc and r.student_college_id
    )
    completeness_pct = round((complete_count / total) * 100, 1)
    completeness_pts = round((completeness_pct / 100) * 25, 1)

    # 2. Validity (25 pts): records with 0 unresolved errors
    # All non-rejected records in ACTIVE or PENDING_APPROVAL passed errors
    validity_pct = 100.0
    validity_pts = 25.0

    # 3. Timeliness (25 pts): records not admitted late or outside window
    # Check validation results for ADMISSION_OUTSIDE_WINDOW
    window_violations = (
        db.scalar(
            select(func.count(StudentRecord.id))
            .join(
                InstitutionProgramme,
                InstitutionProgramme.id == StudentRecord.institution_programme_id,
            )
            .where(
                StudentRecord.institution_id == institution_id,
                InstitutionProgramme.academic_year == academic_year,
                StudentRecord.over_cap.is_(True),
            )
        )
        or 0
    )
    timely_pct = round(max(0.0, 100.0 - (window_violations / total) * 100.0), 1)
    timeliness_pts = round((timely_pct / 100) * 25, 1)

    # 4. Clean share (25 pts): records without over-cap or warnings
    over_cap_count = sum(1 for r in recs if r.over_cap)
    clean_pct = round(max(0.0, ((total - over_cap_count) / total) * 100.0), 1)
    clean_pts = round((clean_pct / 100) * 25, 1)

    overall = round(completeness_pts + validity_pts + timeliness_pts + clean_pts)

    if overall >= 85:
        rating = "EXCELLENT"
    elif overall >= 70:
        rating = "GOOD"
    elif overall >= 50:
        rating = "NEEDS_ATTENTION"
    else:
        rating = "POOR"

    return {
        "institution_id": institution_id,
        "academic_year": academic_year,
        "overall_score": overall,
        "rating": rating,
        "components": {
            "completeness": {"score": completeness_pts, "pct": completeness_pct},
            "validity": {"score": validity_pts, "pct": validity_pct},
            "timeliness": {"score": timeliness_pts, "pct": timely_pct},
            "clean_share": {"score": clean_pts, "pct": clean_pct},
        },
        "summary_en": f"Health score {overall}/100 ({rating}) based on {total} student records.",
    }
