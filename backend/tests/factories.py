"""Small DB factories for Phase 3 tests (synthetic people and records)."""

from __future__ import annotations

import itertools
import random
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt
from app.db.models import (
    Institution,
    InstitutionProgramme,
    MockApaarRegistry,
    Programme,
    RecordStatus,
    Student,
    StudentRecord,
)
from app.services.names import name_keys
from app.services.privacy_tokens import identity_token
from app.services.quota import ledger_key_for
from app.services.rule_engine import suspicious_pattern

TEST_INST_PREFIX = "P3T-"
_rng = random.Random(3)
_seq = itertools.count(1)


def new_apaar() -> str:
    """Synthetic test APAAR IDs: '96' prefix, never a suspicious pattern."""
    while True:
        cand = f"96{_rng.randrange(10**10):010d}"
        if suspicious_pattern(cand) is None:
            return cand


def register(db: Session, name: str, dob: date, gender: str = "M") -> str:
    apaar = new_apaar()
    db.add(MockApaarRegistry(apaar_id=apaar, full_name=name, dob=dob, gender=gender))
    db.flush()
    return apaar


def make_institution(db: Session, suffix: str) -> Institution:
    inst = Institution(
        code=f"{TEST_INST_PREFIX}{suffix}",
        name=f"Phase 3 test institution {suffix}",
        state="Test State",
        approved_flag=True,
    )
    db.add(inst)
    db.flush()
    return inst


def make_ip(
    db: Session,
    inst: Institution,
    prog_code: str,
    intake: int,
    shift: str = "REGULAR",
    year: str = "2025-26",
) -> InstitutionProgramme:
    prog = db.scalar(select(Programme).where(Programme.canonical_code == prog_code))
    ip = InstitutionProgramme(
        institution_id=inst.id,
        programme_id=prog.id,
        academic_year=year,
        shift=shift,
        approved_intake=intake,
        declared_enrolment=intake,
    )
    db.add(ip)
    db.flush()
    return ip


def make_record(
    db: Session,
    ip: InstitutionProgramme,
    category: str = "GENERAL",
    entry: str = "REGULAR",
    admission: date = date(2025, 8, 1),
    status: RecordStatus = RecordStatus.PENDING_APPROVAL,
) -> int:
    """A stored record that has not taken a seat yet."""
    n = next(_seq)
    name = f"Test Student {n}"
    apaar = new_apaar()
    keys = name_keys(name)
    student = Student(
        apaar_id=apaar,
        name_enc=encrypt(name),
        name_norm=keys.normalised,
        dob=date(2007, 1, 1),
        gender="M",
        identity_token=identity_token(name, date(2007, 1, 1), apaar),
    )
    db.add(student)
    db.flush()
    rec = StudentRecord(
        student_id=student.id,
        institution_id=ip.institution_id,
        institution_programme_id=ip.id,
        admission_date=admission,
        admission_category=category,
        entry_type=entry,
        mode="FULL_TIME",
        ledger_key=ledger_key_for(category, entry),
        status=status,
    )
    db.add(rec)
    db.flush()
    return rec.id


def drop_test_institutions(db: Session) -> None:
    db.execute(delete(Institution).where(Institution.code.like(f"{TEST_INST_PREFIX}%")))
    db.execute(delete(Student).where(Student.apaar_id.like("96%")))
    db.execute(delete(MockApaarRegistry).where(MockApaarRegistry.apaar_id.like("96%")))
