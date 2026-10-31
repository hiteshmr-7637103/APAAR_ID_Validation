"""Write a generated Dataset to PostgreSQL (used by POST /demo/seed and the start-up seed).

`seed_national()` = reset generated data + Phase 1 seed + bulk load. It is deterministic in
content for a given config (Fernet ciphertexts differ run to run because Fernet uses a
random IV; the decrypted values are identical).

`seed_national()` then replays the records through the rule engine and Seat Ledger
(`app/synth/replay.py`).

Reset keeps: users, the Phase 1 demo institutions/programmes/institution-programme rows,
the programme catalogue, signing keys and the audit log. Everything else generated or
derived (students, records, ledger, cases, consent, MOCK registry, labels, quota rules) is
wiped and rebuilt.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, insert, select, text, tuple_
from sqlalchemy.orm import Session

from app.core.crypto import encrypt
from app.db import seed as phase1
from app.db.models import (
    GroundTruthLabel,
    Institution,
    InstitutionProgramme,
    MockApaarRegistry,
    Programme,
    QuotaRule,
    Student,
    StudentRecord,
    StudentRecordVersion,
)
from app.services.names import name_keys
from app.services.privacy_tokens import (
    address_token,
    contact_token,
    email_token,
    identity_token,
)
from app.services.quota import DEFAULT_QUOTA_RULES
from app.synth.generator import Dataset, GeneratorConfig, generate

SYNTHETIC_PREFIX = "SYN-"

# Tables wiped on every national reseed (order irrelevant: one TRUNCATE ... CASCADE).
_RESET_TABLES = (
    "ground_truth_labels",
    "mock_apaar_registry",
    "ml_feedback",
    "cases",
    "validation_results",
    "seat_ledger_entries",
    "seat_ledger_balances",
    "enrolment_confirmations",
    "trust_events",
    "student_record_versions",
    "verification_events",
    "consent_requests",
    "notifications",
    "revocations",
    "student_records",
    "students",
    "upload_chunks",
    "upload_batches",
    "quota_rules",
)

_BATCH = 2000


def reset_generated_data(db: Session) -> None:
    """Remove the national synthetic dataset, leaving the Phase 1 seed in place."""
    db.execute(text(f"TRUNCATE {', '.join(_RESET_TABLES)} RESTART IDENTITY CASCADE"))
    demo_codes = [i["code"] for i in phase1.INSTITUTIONS]
    keep = {
        (code, prog, phase1.ACADEMIC_YEAR, shift.value)
        for code, prog, shift, _, _ in phase1.INSTITUTION_PROGRAMMES
    }
    rows = db.execute(
        select(
            InstitutionProgramme.id,
            Institution.code,
            Programme.canonical_code,
            InstitutionProgramme.academic_year,
            InstitutionProgramme.shift,
        )
        .join(Institution, Institution.id == InstitutionProgramme.institution_id)
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
        .where(Institution.code.in_(demo_codes))
    ).all()
    stale = [r.id for r in rows if (r.code, r.canonical_code, r.academic_year, r.shift) not in keep]
    if stale:
        db.execute(delete(InstitutionProgramme).where(InstitutionProgramme.id.in_(stale)))
    db.execute(delete(Institution).where(Institution.code.like(f"{SYNTHETIC_PREFIX}%")))
    db.flush()


def _bulk(db: Session, model, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _BATCH):
        db.execute(insert(model), rows[i : i + _BATCH])


def _sync_sequence(db: Session, table: str) -> None:
    db.execute(
        text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        )
    )


def load_dataset(db: Session, ds: Dataset) -> dict[str, Any]:
    """Insert `ds` into an empty (reset) repository. Commits."""
    phase1.seed(db)  # demo institutions, programme catalogue, demo IP rows, demo accounts

    inst_ids = {i.code: i.id for i in db.scalars(select(Institution))}
    new_insts = [
        Institution(
            code=i.code,
            name=i.name,
            state=i.state,
            district=i.district,
            type=i.type,
            approved_flag=True,
        )
        for i in ds.institutions
        if i.code not in inst_ids
    ]
    db.add_all(new_insts)
    db.flush()
    inst_ids.update({i.code: i.id for i in new_insts})

    prog_ids = {p.canonical_code: p.id for p in db.scalars(select(Programme))}
    existing = {
        (r.code, r.canonical_code, r.academic_year, r.shift): r.id
        for r in db.execute(
            select(
                InstitutionProgramme.id,
                Institution.code,
                Programme.canonical_code,
                InstitutionProgramme.academic_year,
                InstitutionProgramme.shift,
            )
            .join(Institution, Institution.id == InstitutionProgramme.institution_id)
            .join(Programme, Programme.id == InstitutionProgramme.programme_id)
            .where(
                tuple_(Institution.code, Programme.canonical_code).in_(
                    [(ip.key[0], ip.key[1]) for ip in ds.ips]
                )
            )
        ).all()
    }
    ip_ids: dict[tuple, int] = {}
    new_ips: list[tuple[tuple, InstitutionProgramme]] = []
    for ip in ds.ips:
        if ip.key in existing:
            ip_ids[ip.key] = existing[ip.key]
            continue
        obj = InstitutionProgramme(
            institution_id=inst_ids[ip.key[0]],
            programme_id=prog_ids[ip.key[1]],
            academic_year=ip.key[2],
            shift=ip.key[3],
            approved_intake=ip.approved_intake,
            declared_enrolment=ip.declared_enrolment,
        )
        new_ips.append((ip.key, obj))
    db.add_all([obj for _, obj in new_ips])
    db.flush()
    ip_ids.update({key: obj.id for key, obj in new_ips})

    _bulk(
        db,
        MockApaarRegistry,
        [
            dict(apaar_id=r.apaar_id, full_name=r.full_name, dob=r.dob, gender=r.gender)
            for r in ds.registry
        ],
    )

    student_rows = []
    for s in ds.students:
        keys = name_keys(s.name)
        student_rows.append(
            dict(
                id=s.key,
                apaar_id=s.apaar_id,
                name_enc=encrypt(s.name),
                name_norm=keys.normalised,
                name_phonetic=" ".join(keys.phonetic),
                dob=s.dob,
                gender=s.gender,
                identity_token=identity_token(s.name, s.dob, s.apaar_id),
            )
        )
    _bulk(db, Student, student_rows)

    apaar_by_student = {s.key: s.apaar_id for s in ds.students}
    record_rows, version_rows = [], []
    for r in ds.records:
        ip_id = ip_ids[r.ip_key]
        record_rows.append(
            dict(
                id=r.key,
                student_id=r.student_key,
                institution_id=inst_ids[r.ip_key[0]],
                institution_programme_id=ip_id,
                student_college_id=r.student_college_id,
                admission_date=r.admission_date,
                end_date=r.end_date,
                admission_category=r.category.value,
                entry_type=r.entry_type.value,
                mode=r.mode.value,
                ledger_key=r.ledger_key.value,
                nationality=r.nationality,
                is_nri=r.is_nri,
                nri_sponsor_enc=encrypt(r.nri_sponsor),
                is_oci=r.is_oci,
                oci_number_enc=encrypt(r.oci_number),
                is_foreign_national=r.is_foreign_national,
                id_document_note_enc=encrypt(r.id_document_note),
                status=r.status.value,
                trust_tier=0,
                phone_enc=encrypt(r.phone),
                email_enc=encrypt(r.email),
                address_enc=encrypt(r.address),
                contact_token=contact_token(r.phone),
                email_token=email_token(r.email),
                address_token=address_token(r.address),
                current_version=1,
            )
        )
        version_rows.append(
            dict(
                student_record_id=r.key,
                version=1,
                reason="synthetic national seed",
                data_json={
                    "source": "synthetic_seed",
                    "seed": ds.config.seed,
                    "apaar_id": apaar_by_student[r.student_key],
                    "institution_code": r.ip_key[0],
                    "programme_code": r.ip_key[1],
                    "academic_year": r.ip_key[2],
                    "shift": r.ip_key[3],
                    "student_college_id": r.student_college_id,
                    "admission_date": r.admission_date.isoformat(),
                    "end_date": r.end_date.isoformat() if r.end_date else None,
                    "admission_category": r.category.value,
                    "entry_type": r.entry_type.value,
                    "mode": r.mode.value,
                    "ledger_key": r.ledger_key.value,
                    "status": r.status.value,
                    "nationality": r.nationality,
                    "is_nri": r.is_nri,
                    "is_oci": r.is_oci,
                    "is_foreign_national": r.is_foreign_national,
                    "pii": "encrypted in student_records / students",
                },
            )
        )
    _bulk(db, StudentRecord, record_rows)
    _bulk(db, StudentRecordVersion, version_rows)

    _bulk(
        db,
        GroundTruthLabel,
        [
            dict(
                seed=ds.config.seed,
                scenario=lbl.scenario,
                group_id=lbl.group_id,
                role=lbl.role,
                corruption_types=lbl.corruption_types,
                student_record_id=lbl.record_key,
                student_id=lbl.student_key,
                institution_id=inst_ids[lbl.inst_code] if lbl.inst_code else None,
                institution_programme_id=ip_ids[lbl.ip_key] if lbl.ip_key else None,
                detail_json=lbl.detail,
            )
            for lbl in ds.labels
        ],
    )

    db.add_all(
        QuotaRule(key=key, value_json=ds.quota_rules[key], description=desc, version=1)
        for key, (_, desc) in DEFAULT_QUOTA_RULES.items()
    )
    for table in ("students", "student_records"):
        _sync_sequence(db, table)
    db.commit()
    return ds.summary()


def seed_national(
    db: Session, config: GeneratorConfig | None = None, *, replay: bool = True
) -> dict[str, Any]:
    """Reset and regenerate the deterministic national dataset, then (Phase 3) replay it
    through the rule engine and Seat Ledger so flags, cases and tiers come from real code."""
    from app.synth.replay import replay_seeded_records

    t0 = time.perf_counter()
    ds = generate(config or GeneratorConfig())
    t1 = time.perf_counter()
    reset_generated_data(db)
    summary = load_dataset(db, ds)
    t2 = time.perf_counter()
    summary["timings_s"] = {"generate": round(t1 - t0, 2), "load": round(t2 - t1, 2)}
    if replay:
        result = replay_seeded_records(db)
        summary["timings_s"]["replay"] = result.pop("timings_s")
        summary["replay"] = result
    return summary


def national_dataset_present(db: Session) -> bool:
    return db.scalar(select(StudentRecord.id).limit(1)) is not None
