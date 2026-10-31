"""Phase 2 against PostgreSQL: POST /demo/seed, the MOCK APAAR registry, encryption at rest,
privacy tokens and ground-truth labels.

The module seeds the full default national dataset once (the Phase 2 "done when" check) and
resets back to the Phase 1 seed afterwards, so other test modules see the state they expect.
"""

from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import func, select

from app.core.crypto import decrypt
from app.db.models import (
    GroundTruthLabel,
    Institution,
    InstitutionProgramme,
    MockApaarRegistry,
    QuotaRule,
    Student,
    StudentRecord,
    StudentRecordVersion,
)
from app.db.seed import DEMO_STUDENT_APAAR_ID
from app.db.session import SessionLocal
from app.services.apaar_client import MockApaarClient, get_apaar_client
from app.services.names import normalise_name
from app.services.privacy_tokens import contact_token
from app.synth.generator import GeneratorConfig
from app.synth.loader import reset_generated_data, seed_national
from tests.conftest import API


def _count(db, model, *where):
    stmt = select(func.count()).select_from(model)
    for w in where:
        stmt = stmt.where(w)
    return db.scalar(stmt)


@pytest.fixture(scope="module")
def national(client, auth):
    r = client.post(f"{API}/demo/seed", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    yield r.json()
    with SessionLocal() as db:
        reset_generated_data(db)
        db.commit()


def test_demo_seed_requires_admin(client, auth):
    for who in ("AICTE_REVIEWER", "INST_MAKER", "INST_CHECKER", "VERIFIER", "STUDENT"):
        assert client.post(f"{API}/demo/seed", headers=auth(who)).status_code == 403, who
    assert client.post(f"{API}/demo/seed").status_code == 401


def test_demo_seed_rejects_out_of_range_sizes(client, auth):
    r = client.post(f"{API}/demo/seed?students=60000", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 422


def test_national_seed_counts(national):
    """Phase 2 done-when: 40 institutions and about 20k records with headcount-only ones."""
    assert national["institutions"] == 40 and national["states"] == 8
    assert 18_000 <= national["student_records"] <= 23_000
    assert national["headcount_only_institutions"] >= 10
    with SessionLocal() as db:
        assert _count(db, Institution) == 40
        assert len(set(db.scalars(select(Institution.state)))) == 8
        assert _count(db, StudentRecord) == national["student_records"]
        assert _count(db, Student) == national["students"]
        assert _count(db, StudentRecordVersion) == national["student_records"]
        assert _count(db, MockApaarRegistry) == national["registry_entries"]
        assert _count(db, InstitutionProgramme) == national["institution_programmes"]
        assert _count(db, GroundTruthLabel) == sum(national["labels"].values())
        head = _count(db, GroundTruthLabel, GroundTruthLabel.scenario == "HEADCOUNT_ONLY")
        assert head == national["headcount_only_institutions"]
        # Records stored as already-approved history. Since Phase 3 the seed replays them
        # through the rule engine: tiers are T0 (an ERROR), T1, or (Phase 4) T2 when the
        # APAAR ID matches the MOCK registry; never T3 (student confirmation, Phase 7).
        statuses = Counter(db.scalars(select(StudentRecord.status)))
        assert set(statuses) <= {"ACTIVE", "WITHDRAWN", "GRADUATED"}
        assert db.scalar(select(func.max(StudentRecord.trust_tier))) == 2


def test_coverage_of_headcount_only_institutions_in_db(national):
    with SessionLocal() as db:
        for lbl in db.scalars(
            select(GroundTruthLabel).where(GroundTruthLabel.scenario == "HEADCOUNT_ONLY")
        ):
            declared = db.scalar(
                select(func.sum(InstitutionProgramme.declared_enrolment)).where(
                    InstitutionProgramme.institution_id == lbl.institution_id,
                    InstitutionProgramme.academic_year == "2025-26",
                )
            )
            received = _count(
                db,
                StudentRecord,
                StudentRecord.institution_id == lbl.institution_id,
                StudentRecord.institution_programme_id.in_(
                    select(InstitutionProgramme.id).where(
                        InstitutionProgramme.academic_year == "2025-26"
                    )
                ),
            )
            assert 0.30 <= received / declared <= 0.60


def test_pii_is_encrypted_at_rest(national):
    with SessionLocal() as db:
        # (an OCI record that has its number: a few are injected OCI_NO_NUMBER faults)
        rec = db.scalar(
            select(StudentRecord)
            .where(StudentRecord.is_oci, StudentRecord.oci_number_enc.is_not(None))
            .order_by(StudentRecord.id)
            .limit(1)
        )
        student = db.get(Student, rec.student_id)
        phone = decrypt(rec.phone_enc)
        assert phone.isdigit() and len(phone) == 10 and phone not in rec.phone_enc
        assert decrypt(rec.email_enc).endswith("@mail.example")
        assert "," in decrypt(rec.address_enc)
        assert decrypt(rec.oci_number_enc).startswith("A")
        name = decrypt(student.name_enc)
        assert name and name not in student.name_enc
        assert student.name_norm == normalise_name(name)
        assert rec.contact_token == contact_token(phone)
        nri = db.scalar(
            select(StudentRecord).where(StudentRecord.nri_sponsor_enc.is_not(None)).limit(1)
        )
        assert decrypt(nri.nri_sponsor_enc)


def test_ring_members_share_one_contact_token(national):
    with SessionLocal() as db:
        rows = db.execute(
            select(GroundTruthLabel.group_id, StudentRecord.contact_token)
            .join(StudentRecord, StudentRecord.id == GroundTruthLabel.student_record_id)
            .where(GroundTruthLabel.scenario == "RING")
        ).all()
    rings: dict[str, set] = {}
    for group, token in rows:
        rings.setdefault(group, set()).add(token)
    assert rings and all(len(tokens) == 1 for tokens in rings.values())


def test_quota_rules_seeded_with_verify_defaults(national):
    with SessionLocal() as db:
        rules = {r.key: r for r in db.scalars(select(QuotaRule))}
    fn = rules["bucket.SUPERNUMERARY_FN_OCI_PIO_CIWGC"].value_json
    assert fn["pct"] == 0.15 and fn["verify"] is True
    assert rules["bucket.LATERAL"].value_json["pct"] == 0.20
    assert rules["bucket.NRI"].value_json["default"] == 0
    assert rules["bucket.NRI"].value_json["by_institution_programme"]
    assert all(r.version == 1 for r in rules.values())


# ----------------------------------------------------------------------------- MOCK APAAR


def test_mock_registry_lookup(client, auth, national):
    r = client.get(f"{API}/mock/apaar/{DEMO_STUDENT_APAAR_ID}", headers=auth("AICTE_REVIEWER"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["apaar_id"] == DEMO_STUDENT_APAAR_ID
    assert body["full_name"] == "Ananya Rao" and body["dob"] == "2007-05-14"
    assert body["gender"] == "F"
    assert body["source"] == "MOCK" and "MOCK" in body["notice"]


def test_mock_registry_matches_seeded_records(client, auth, national):
    with SessionLocal() as db:
        regs = db.scalars(select(MockApaarRegistry).order_by(MockApaarRegistry.apaar_id).limit(5))
        for reg in regs:
            r = client.get(f"{API}/mock/apaar/{reg.apaar_id}", headers=auth("AICTE_ADMIN"))
            assert r.status_code == 200
            assert r.json()["full_name"] == reg.full_name
            assert r.json()["dob"] == reg.dob.isoformat()


def test_mock_registry_unknown_and_invalid_ids(client, auth, national):
    r = client.get(f"{API}/mock/apaar/123456789012", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "APAAR_NOT_FOUND"
    for bad in ("12345", "99027483165X", "9902748316501"):
        r = client.get(f"{API}/mock/apaar/{bad}", headers=auth("AICTE_ADMIN"))
        assert r.status_code == 422, bad
        assert r.json()["error"]["code"] == "APAAR_INVALID"


def test_mock_registry_is_aicte_only(client, auth, national):
    url = f"{API}/mock/apaar/{DEMO_STUDENT_APAAR_ID}"
    for who in ("INST_MAKER", "INST_CHECKER", "VERIFIER", "STUDENT"):
        assert client.get(url, headers=auth(who)).status_code == 403, who
    assert client.get(url).status_code == 401


def test_apaar_client_interface(national):
    with SessionLocal() as db:
        client = get_apaar_client(db)
        assert isinstance(client, MockApaarClient)
        rec = client.lookup(DEMO_STUDENT_APAAR_ID)
        assert rec is not None and rec.source == "MOCK"
        assert client.lookup("123456789012") is None
        assert client.lookup("not-an-id") is None
        many = client.lookup_many([DEMO_STUDENT_APAAR_ID, "123456789012", "x"])
        assert list(many) == [DEMO_STUDENT_APAAR_ID]


# ----------------------------------------------------------------------------- reseed


def test_reseed_is_deterministic_and_resets(national):
    """A second (smaller) seed replaces the dataset; the same config twice gives the same
    logical content."""
    config = GeneratorConfig(seed=42, n_institutions=8, target_students=1500)

    def snapshot():
        with SessionLocal() as db:
            summary = seed_national(db, config)
            rows = db.execute(
                select(Student.apaar_id, Student.name_norm, Student.dob).order_by(Student.id)
            ).all()
            n_inst = _count(db, Institution)
        summary.pop("timings_s")
        return summary, rows, n_inst

    first, second = snapshot(), snapshot()
    assert first == second
    assert first[2] == 8
