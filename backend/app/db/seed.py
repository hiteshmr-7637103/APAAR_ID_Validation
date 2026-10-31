"""Idempotent Phase 1 seed: demo institutions, programmes and one account per role.

Run with `python -m app.db.seed` (the API container runs it on every start;
existing rows are left untouched, missing ones are created).
`python -m app.db.seed --national-if-empty` also generates the synthetic national dataset
(40 institutions, ~20k students, SEED=42) when no student records exist yet; the same
dataset can be rebuilt at any time with POST /api/v1/demo/seed.

All names and codes below are SYNTHETIC demo data, not real institutions or real
AICTE programme codes.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Institution,
    InstitutionProgramme,
    Programme,
    ProgrammeLevel,
    Role,
    Shift,
    User,
)
from app.db.session import SessionLocal

ACADEMIC_YEAR = "2025-26"

# Placeholder APAAR ID for the demo student. "99" prefix = clearly synthetic (spec section 10).
# The MOCK APAAR registry (Phase 2) must contain this ID.
DEMO_STUDENT_APAAR_ID = "990274831650"

INSTITUTIONS = [
    dict(
        code="DEMO-INST-001",
        name="Demo Institute of Technology, Pune",
        state="Maharashtra",
        district="Pune",
        type="Engineering (Private, demo)",
    ),
    dict(
        code="DEMO-INST-002",
        name="Demo School of Management, Bengaluru",
        state="Karnataka",
        district="Bengaluru Urban",
        type="Management / PGDM (demo)",
    ),
    dict(
        code="DEMO-INST-003",
        name="Demo Polytechnic, Jaipur",
        state="Rajasthan",
        district="Jaipur",
        type="Diploma (Government, demo)",
    ),
]

PROGRAMMES = [
    dict(
        canonical_code="UG-CSE",
        name="B.Tech Computer Science and Engineering",
        level=ProgrammeLevel.UG,
        duration_years=4,
    ),
    dict(
        canonical_code="UG-ECE",
        name="B.Tech Electronics and Communication Engineering",
        level=ProgrammeLevel.UG,
        duration_years=4,
    ),
    dict(
        canonical_code="UG-ME",
        name="B.Tech Mechanical Engineering",
        level=ProgrammeLevel.UG,
        duration_years=4,
    ),
    dict(
        canonical_code="PG-MBA",
        name="Master of Business Administration",
        level=ProgrammeLevel.PG,
        duration_years=2,
    ),
    dict(
        canonical_code="PGDM-GEN",
        name="Post Graduate Diploma in Management",
        level=ProgrammeLevel.PGDM,
        duration_years=2,
    ),
    dict(
        canonical_code="MCA",
        name="Master of Computer Applications",
        level=ProgrammeLevel.MCA,
        duration_years=2,
    ),
    dict(
        canonical_code="DIP-CE",
        name="Diploma in Civil Engineering",
        level=ProgrammeLevel.DIPLOMA,
        duration_years=3,
    ),
    dict(
        canonical_code="DIP-EE",
        name="Diploma in Electrical Engineering",
        level=ProgrammeLevel.DIPLOMA,
        duration_years=3,
    ),
    # Phase 2 additions for the national synthetic dataset (codes are synthetic, not AICTE's).
    *[
        dict(canonical_code=code, name=name, level=level, duration_years=years)
        for code, name, level, years in [
            ("UG-CE", "B.Tech Civil Engineering", ProgrammeLevel.UG, 4),
            ("UG-EE", "B.Tech Electrical Engineering", ProgrammeLevel.UG, 4),
            ("UG-IT", "B.Tech Information Technology", ProgrammeLevel.UG, 4),
            ("UG-AIDS", "B.Tech Artificial Intelligence and Data Science", ProgrammeLevel.UG, 4),
            ("PG-MTECH-CSE", "M.Tech Computer Science and Engineering", ProgrammeLevel.PG, 2),
            ("PGDM-FIN", "PGDM (Finance)", ProgrammeLevel.PGDM, 2),
            ("PGDM-MKT", "PGDM (Marketing)", ProgrammeLevel.PGDM, 2),
            ("PGDM-HR", "PGDM (Human Resource Management)", ProgrammeLevel.PGDM, 2),
            ("PGDM-BA", "PGDM (Business Analytics)", ProgrammeLevel.PGDM, 2),
            ("PGDM-IB", "PGDM (International Business)", ProgrammeLevel.PGDM, 2),
            ("DIP-ME", "Diploma in Mechanical Engineering", ProgrammeLevel.DIPLOMA, 3),
            ("DIP-CSE", "Diploma in Computer Engineering", ProgrammeLevel.DIPLOMA, 3),
            ("DIP-ECE", "Diploma in Electronics Engineering", ProgrammeLevel.DIPLOMA, 3),
            ("DIP-AUTO", "Diploma in Automobile Engineering", ProgrammeLevel.DIPLOMA, 3),
        ]
    ],
]

# (institution code, programme code, shift, approved_intake, declared_enrolment)
INSTITUTION_PROGRAMMES = [
    ("DEMO-INST-001", "UG-CSE", Shift.REGULAR, 120, 118),
    ("DEMO-INST-001", "UG-CSE", Shift.SECOND, 60, 55),
    ("DEMO-INST-001", "UG-ECE", Shift.REGULAR, 60, 57),
    ("DEMO-INST-001", "UG-ME", Shift.REGULAR, 60, 48),
    ("DEMO-INST-001", "MCA", Shift.REGULAR, 60, 60),
    ("DEMO-INST-002", "PGDM-GEN", Shift.REGULAR, 180, 176),
    ("DEMO-INST-002", "PG-MBA", Shift.REGULAR, 120, 109),
    ("DEMO-INST-002", "PGDM-FIN", Shift.REGULAR, 60, 57),
    ("DEMO-INST-003", "DIP-CE", Shift.REGULAR, 60, 52),
    ("DEMO-INST-003", "DIP-CE", Shift.SECOND, 60, 41),
    ("DEMO-INST-003", "DIP-EE", Shift.REGULAR, 60, 58),
    ("DEMO-INST-003", "DIP-ME", Shift.REGULAR, 60, 49),
]

# (email, full name, role, institution code or None, apaar id or None)
DEMO_USERS = [
    ("admin@aicte.demo", "Demo AICTE Admin", Role.AICTE_ADMIN, None, None),
    ("reviewer@aicte.demo", "Demo AICTE Reviewer", Role.AICTE_REVIEWER, None, None),
    ("maker@inst1.demo", "Demo Data-entry Operator", Role.INST_MAKER, "DEMO-INST-001", None),
    ("checker@inst1.demo", "Demo Registrar", Role.INST_CHECKER, "DEMO-INST-001", None),
    ("verifier@employer.demo", "Demo Employer Verifier", Role.VERIFIER, None, None),
    ("student@student.demo", "Demo Student", Role.STUDENT, None, DEMO_STUDENT_APAAR_ID),
]


def seed(db: Session) -> dict[str, int]:
    created = {"institutions": 0, "programmes": 0, "institution_programmes": 0, "users": 0}

    insts: dict[str, Institution] = {}
    for data in INSTITUTIONS:
        inst = db.scalar(select(Institution).where(Institution.code == data["code"]))
        if inst is None:
            inst = Institution(approved_flag=True, **data)
            db.add(inst)
            created["institutions"] += 1
        insts[data["code"]] = inst

    progs: dict[str, Programme] = {}
    for data in PROGRAMMES:
        prog = db.scalar(
            select(Programme).where(Programme.canonical_code == data["canonical_code"])
        )
        if prog is None:
            prog = Programme(**data)
            db.add(prog)
            created["programmes"] += 1
        progs[data["canonical_code"]] = prog
    db.flush()

    for inst_code, prog_code, shift, intake, declared in INSTITUTION_PROGRAMMES:
        inst, prog = insts[inst_code], progs[prog_code]
        exists = db.scalar(
            select(InstitutionProgramme.id).where(
                InstitutionProgramme.institution_id == inst.id,
                InstitutionProgramme.programme_id == prog.id,
                InstitutionProgramme.academic_year == ACADEMIC_YEAR,
                InstitutionProgramme.shift == shift,
            )
        )
        if exists is None:
            db.add(
                InstitutionProgramme(
                    institution_id=inst.id,
                    programme_id=prog.id,
                    academic_year=ACADEMIC_YEAR,
                    shift=shift,
                    approved_intake=intake,
                    declared_enrolment=declared,
                )
            )
            created["institution_programmes"] += 1

    password_hash = None
    for email, full_name, role, inst_code, apaar in DEMO_USERS:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            continue
        password_hash = password_hash or hash_password(settings.demo_password)
        db.add(
            User(
                email=email,
                full_name=full_name,
                role=role,
                password_hash=password_hash,
                institution_id=insts[inst_code].id if inst_code else None,
                student_apaar_id=apaar,
                active=True,
            )
        )
        created["users"] += 1

    db.commit()
    return created


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--national-if-empty",
        action="store_true",
        help="also generate the synthetic national dataset if no student records exist",
    )
    parser.add_argument("--national", action="store_true", help="(re)generate it regardless")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        created = seed(db)
        print(f"[seed] created: {created} (existing rows left unchanged)")
        if args.national or (args.national_if_empty and settings.auto_seed_national):
            from app.synth.generator import GeneratorConfig
            from app.synth.loader import national_dataset_present, seed_national

            if args.national or not national_dataset_present(db):
                config = GeneratorConfig(
                    seed=settings.synth_seed,
                    n_institutions=settings.synth_institutions,
                    target_students=settings.synth_students,
                )
                print("[seed] generating the synthetic national dataset ...", flush=True)
                print(f"[seed] national dataset: {seed_national(db, config)}")
            else:
                print("[seed] national dataset already present; left unchanged")


if __name__ == "__main__":
    main()
