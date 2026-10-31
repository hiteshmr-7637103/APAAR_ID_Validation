"""Phase 4: chunked upload, dry run with projected bucket impact, submit, maker-checker,
rejection releases seats, idempotency, trust tiers T0 -> T1 -> T2, versioning (FR-2,
FR-3, FR-4, FR-17, FR-18; spec 13)."""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    Case,
    CaseStatus,
    Institution,
    Role,
    SeatLedgerBalance,
    SeatLedgerEntry,
    Student,
    StudentRecord,
    StudentRecordVersion,
    TrustEvent,
    UploadBatch,
    User,
    ValidationResult,
)
from app.db.session import SessionLocal
from app.services.uploads import TEMPLATE_COLUMNS
from app.synth.loader import reset_generated_data
from tests.conftest import API, login
from tests.factories import drop_test_institutions, register

PROMOTED_EMAIL = "maker2@inst1.test"


@pytest.fixture(scope="module", autouse=True)
def _clean_state():
    with SessionLocal() as db:
        reset_generated_data(db)
        db.commit()
    yield
    with SessionLocal() as db:
        drop_test_institutions(db)
        reset_generated_data(db)
        db.commit()


@pytest.fixture(scope="module")
def inst_ids():
    with SessionLocal() as db:
        return {i.code: i.id for i in db.scalars(select(Institution))}


def row(name="Kavya Iyer", dob=date(2007, 4, 2), gender="F", registered=True, **kw) -> dict:
    """One CSV row (spec 7.2 columns) for a synthetic person in the MOCK registry."""
    apaar = ""
    if registered:
        with SessionLocal() as db:
            apaar = register(db, name, dob, gender)
            db.commit()
    r = dict(
        apaar_id=apaar,
        student_college_id="COL/1",
        full_name=name,
        dob=dob.isoformat(),
        gender=gender,
        programme_code="UG-CSE",
        shift="REGULAR",
        admission_date="2025-08-05",
        admission_category="GENERAL",
        entry_type="REGULAR",
        mode="FULL_TIME",
        nationality="Indian",
        is_nri="false",
        nri_sponsor="",
        is_oci="false",
        oci_number="",
        is_foreign_national="false",
        phone="9999000001",
        email="test.student@mail.example",
        address="1 Test Road, Pune",
    )
    r.update(kw)
    return r


def to_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(TEMPLATE_COLUMNS), lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def upload(client, h, data: bytes, chunks: int = 3) -> int:
    size = max(1, -(-len(data) // chunks))
    parts = [data[i : i + size] for i in range(0, len(data), size)]
    r = client.post(
        f"{API}/uploads", json={"filename": "t.csv", "total_chunks": len(parts)}, headers=h
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    for i in reversed(range(len(parts))):  # out of order on purpose
        r = client.put(f"{API}/uploads/{uid}/chunks/{i}", content=parts[i], headers=h)
        assert r.status_code == 200, r.text
    r = client.post(f"{API}/uploads/{uid}/finalize", headers=h)
    assert r.status_code == 200, r.text
    return uid


def counts():
    with SessionLocal() as db:
        return {
            "records": db.scalar(select(func.count()).select_from(StudentRecord)),
            "students": db.scalar(select(func.count()).select_from(Student)),
            "ledger": db.scalar(select(func.count()).select_from(SeatLedgerEntry)),
            "used": db.scalar(select(func.coalesce(func.sum(SeatLedgerBalance.used), 0))),
            "versions": db.scalar(select(func.count()).select_from(StudentRecordVersion)),
            "cases": db.scalar(select(func.count()).select_from(Case)),
            "events": db.scalar(select(func.count()).select_from(TrustEvent)),
        }


def bucket_used(ip_code: str, key: str, inst="DEMO-INST-001", shift="REGULAR") -> int:
    from app.db.models import InstitutionProgramme, Programme

    with SessionLocal() as db:
        used = db.scalar(
            select(SeatLedgerBalance.used)
            .join(
                InstitutionProgramme,
                InstitutionProgramme.id == SeatLedgerBalance.institution_programme_id,
            )
            .join(Programme, Programme.id == InstitutionProgramme.programme_id)
            .join(Institution, Institution.id == InstitutionProgramme.institution_id)
            .where(
                Institution.code == inst,
                Programme.canonical_code == ip_code,
                InstitutionProgramme.shift == shift,
                InstitutionProgramme.academic_year == "2025-26",
                SeatLedgerBalance.ledger_key == key,
            )
        )
        return used or 0


# --------------------------------------------------------------------------- template, chunks


def test_template_has_spec_columns(client, auth):
    r = client.get(f"{API}/templates/students.csv", headers=auth("INST_MAKER"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.strip().split(",") == [
        "apaar_id",
        "student_college_id",
        "full_name",
        "dob",
        "gender",
        "programme_code",
        "shift",
        "admission_date",
        "admission_category",
        "entry_type",
        "mode",
        "nationality",
        "is_nri",
        "nri_sponsor",
        "is_oci",
        "oci_number",
        "is_foreign_national",
        "phone",
        "email",
        "address",
    ]
    assert client.get(f"{API}/templates/students.csv", headers=auth("VERIFIER")).status_code == 403


def test_chunked_upload_resume_and_idempotent_finalize(client, auth):
    h = auth("INST_MAKER")
    data = to_csv([row(name="Chunk Person"), row(name="Chunk Other")])
    third = -(-len(data) // 3)
    parts = [data[:third], data[third : 2 * third], data[2 * third :]]
    uid = client.post(f"{API}/uploads", json={"total_chunks": 3}, headers=h).json()["id"]
    before = counts()
    assert client.put(f"{API}/uploads/{uid}/chunks/0", content=parts[0], headers=h).is_success
    assert client.put(f"{API}/uploads/{uid}/chunks/2", content=b"garbage", headers=h).is_success
    # incomplete: chunk 1 missing
    r = client.post(f"{API}/uploads/{uid}/finalize", headers=h)
    assert r.status_code == 409 and r.json()["error"]["details"]["missing"] == [1]
    # resume: the status tells the client what is still missing; re-send chunk 2 correctly
    assert client.get(f"{API}/uploads/{uid}", headers=h).json()["received_chunks"] == [0, 2]
    r = client.put(f"{API}/uploads/{uid}/chunks/2", content=parts[2], headers=h)
    assert r.json()["status"] == "replaced"
    assert client.put(f"{API}/uploads/{uid}/chunks/1", content=parts[1], headers=h).is_success
    assert client.put(f"{API}/uploads/{uid}/chunks/3", content=b"x", headers=h).status_code == 422

    first = client.post(f"{API}/uploads/{uid}/finalize", headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["rows"] == 2 and first.json()["idempotent_replay"] is False
    again = client.post(f"{API}/uploads/{uid}/finalize", headers=h)
    assert again.status_code == 200 and again.json()["idempotent_replay"] is True
    assert again.json()["sha256"] == first.json()["sha256"]
    # chunks are frozen after finalize; an identical retry is a no-op
    r = client.put(f"{API}/uploads/{uid}/chunks/1", content=b"changed", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "UPLOAD_FINALIZED"
    r = client.put(f"{API}/uploads/{uid}/chunks/1", content=parts[1], headers=h)
    assert r.status_code == 200 and r.json()["status"] == "unchanged"
    assert counts() == before  # finalize never writes to the repository
    with SessionLocal() as db:
        b = db.get(UploadBatch, uid)
        assert b.csv_enc is not None and b"Chunk Person" not in b.csv_enc  # encrypted at rest


def test_upload_rbac_and_scoping(client, auth):
    h = auth("INST_MAKER")
    uid = client.post(f"{API}/uploads", json={}, headers=h).json()["id"]
    for who in ("INST_CHECKER", "AICTE_ADMIN", "VERIFIER", "STUDENT"):
        assert client.post(f"{API}/uploads", json={}, headers=auth(who)).status_code == 403
    assert client.get(f"{API}/uploads/{uid}", headers=auth("OTHER_MAKER")).status_code == 404
    r = client.put(f"{API}/uploads/{uid}/chunks/0", content=b"a", headers=auth("OTHER_MAKER"))
    assert r.status_code == 404
    assert client.get(f"{API}/uploads/{uid}", headers=auth("INST_CHECKER")).status_code == 200
    assert client.get(f"{API}/uploads/{uid}", headers=auth("AICTE_REVIEWER")).status_code == 200
    assert client.get(f"{API}/batches/{uid}", headers=auth("OTHER_MAKER")).status_code == 404
    assert all(
        b["institution_id"] != uid
        for b in client.get(f"{API}/batches", headers=auth("OTHER_MAKER")).json()
    )


def test_bad_header_is_reported_at_finalize(client, auth):
    h = auth("INST_MAKER")
    uid = client.post(f"{API}/uploads", json={"total_chunks": 1}, headers=h).json()["id"]
    client.put(f"{API}/uploads/{uid}/chunks/0", content=b"apaar_id,name\n1,2\n", headers=h)
    r = client.post(f"{API}/uploads/{uid}/finalize", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CSV_HEADER_INVALID"
    assert "full_name" in r.json()["error"]["details"]["missing_columns"]


# --------------------------------------------------------------------------- dry run -> submit


@pytest.fixture(scope="module")
def faulty_upload(client, auth):
    """A CSV with deliberate faults (demo script step 2)."""
    clean = row(name="Arjun Menon", gender="M")
    rows = [
        clean,  # 1 clean
        row(name="Bad Apaar", registered=False, apaar_id="12345"),  # 2 APAAR_INVALID
        dict(row(name="Date Format"), dob="02/04/2007"),  # 3 FIELD_INVALID date + fix
        dict(clean, student_college_id="COL/dup"),  # 4 same student again in this file
        row(name="Late Entry", admission_date="2025-10-15"),  # 5 WARNING outside window
        row(  # 6 NRI: cap 0 -> projected over cap by 1
            name="Nri One", admission_category="NRI", is_nri="yes", nri_sponsor="Uncle, Oman"
        ),
        row(  # 7 NRI: projected over cap by 2
            name="Nri Two", admission_category="NRI", is_nri="1", nri_sponsor="Father, Doha"
        ),
        row(name="Programme Typo", programme_code="UG-CS"),  # 8 PROGRAMME_NOT_APPROVED
    ]
    return {"rows": rows, "uid": upload(client, auth("INST_MAKER"), to_csv(rows))}


def test_dry_run_reports_rows_fixes_and_bucket_impact_without_writing(client, auth, faulty_upload):
    uid = faulty_upload["uid"]
    before = counts()
    nri_before = bucket_used("UG-CSE", "NRI")
    r = client.post(f"{API}/uploads/{uid}/dry-run", headers=auth("INST_MAKER"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["writes_to_repository"] is False
    assert counts() == before and bucket_used("UG-CSE", "NRI") == nri_before
    rows = {x["row"]: x for x in out["rows"]}
    codes = {n: [o["code"] for o in x["outcomes"]] for n, x in rows.items()}
    assert rows[1]["severity"] == "OK"
    assert "APAAR_INVALID" in codes[2] and rows[2]["severity"] == "ERROR"
    date_err = next(o for o in rows[3]["outcomes"] if o["code"] == "FIELD_INVALID")
    assert date_err["suggested_fix"]["value"] == "2007-04-02"
    assert "REQUIRED_FIELD_MISSING" not in codes[3]  # no double report for the same field
    dup = next(o for o in rows[4]["outcomes"] if o["code"] == "DUP_EXACT_SAME_INST")
    assert "row 1 of this file" in dup["message_en"] and dup["evidence"]["other_row"] == 1
    assert rows[5]["severity"] == "WARNING" and "ADMISSION_OUTSIDE_WINDOW" in codes[5]
    assert all(
        o["suggested_fix"] for x in out["rows"] for o in x["outcomes"] if o["severity"] != "INFO"
    )
    over6 = next(o for o in rows[6]["outcomes"] if o["code"] == "SEAT_OVER_CAP")
    over7 = next(o for o in rows[7]["outcomes"] if o["code"] == "SEAT_OVER_CAP")
    assert over6["message_en"].startswith("Row 6: would exceed NRI by 1")
    assert over7["message_en"].startswith("Row 7: would exceed NRI by 2")
    assert over7["evidence"]["projected"] is True
    sugg = next(o for o in rows[8]["outcomes"] if o["code"] == "PROGRAMME_NOT_APPROVED")
    assert "UG-CSE" in [p["programme_code"] for p in sugg["suggested_fix"]["programmes"]]
    (nri,) = [b for b in out["bucket_impact"] if b["ledger_key"] == "NRI"]
    assert nri["cap"] == 0 and nri["over_cap_rows"] == [6, 7] and nri["excess_after"] == 2
    assert nri["message_en"].startswith("Rows 6, 7: would exceed NRI by 2")
    approved = next(b for b in out["bucket_impact"] if b["ledger_key"] == "APPROVED")
    assert approved["would_be_flagged"] is False and approved["rows_in_file"] == 2
    assert out["summary"] == {
        "rows": 8,
        "rows_with_errors": 4,
        "rows_with_warnings": 3,
        "rows_info_only": 0,
        "rows_clean": 1,
        "rows_submittable": 4,
    }
    # the stored report gives the same picture and filters by severity
    rep = client.get(f"{API}/uploads/{uid}/report?severity=ERROR", headers=auth("INST_CHECKER"))
    assert rep.status_code == 200 and [x["row"] for x in rep.json()["rows"]] == [2, 3, 4, 8]


def test_submit_refuses_errors_and_missing_justification_storing_nothing(
    client, auth, faulty_upload
):
    uid, h = faulty_upload["uid"], auth("INST_MAKER")
    before = counts()
    r = client.post(f"{API}/uploads/{uid}/submit", json={}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "BATCH_HAS_ERRORS"
    assert r.json()["error"]["details"]["error_rows"] == [2, 3, 4, 8]
    r = client.post(f"{API}/uploads/{uid}/submit", json={"exclude_error_rows": True}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "JUSTIFICATION_REQUIRED"
    assert r.json()["error"]["details"]["rows"] == [5]
    assert counts() == before
    assert (
        client.post(f"{API}/uploads/{uid}/submit", headers=auth("INST_CHECKER")).status_code == 403
    )


def test_submit_commits_flags_over_cap_and_is_idempotent(client, auth, faulty_upload):
    uid, h = faulty_upload["uid"], auth("INST_MAKER")
    before = counts()
    nri_before = bucket_used("UG-CSE", "NRI")
    body = {
        "exclude_error_rows": True,
        "row_justifications": {"5": "Admitted late after a court order (document on file)."},
        "justification": "NRI admissions approved by the governing body.",
    }
    r = client.post(f"{API}/uploads/{uid}/submit", json=body, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "PENDING_APPROVAL" and out["stored"] == 4
    assert out["excluded_error_rows"] == [2, 3, 4, 8] and out["over_cap_rows"] == [6, 7]
    after = counts()
    assert after["records"] == before["records"] + 4
    assert bucket_used("UG-CSE", "NRI") == nri_before + 2
    with SessionLocal() as db:
        recs = db.scalars(select(StudentRecord).where(StudentRecord.batch_id == uid)).all()
        assert {r.status for r in recs} == {"PENDING_APPROVAL"}
        assert sum(r.over_cap for r in recs) == 2
        just = db.scalar(
            select(ValidationResult.resolution_note).where(
                ValidationResult.batch_id == uid,
                ValidationResult.code == "ADMISSION_OUTSIDE_WINDOW",
            )
        )
        assert just.startswith("Admitted late")
        case = db.scalar(
            select(Case).where(
                Case.status == CaseStatus.OPEN,
                Case.subject_ids_json["key"].astext.like("SEAT_OVER_CAP:%:NRI"),
                Case.institution_id.is_not(None),
            )
        )
        assert case is not None and case.visibility == "AICTE_ONLY"

    # Idempotency: re-submitting, re-finalizing or re-running the dry run adds nothing.
    again = client.post(f"{API}/uploads/{uid}/submit", json=body, headers=h)
    assert again.status_code == 200 and again.json()["idempotent_replay"] is True
    assert again.json()["stored"] == 4
    assert client.post(f"{API}/uploads/{uid}/finalize", headers=h).json()["idempotent_replay"]
    assert client.post(f"{API}/uploads/{uid}/dry-run", headers=h).status_code == 409
    assert counts() == after

    # The same file uploaded again cannot create duplicates: the rows are exact duplicates.
    uid2 = upload(client, h, to_csv(faulty_upload["rows"]))
    rows = {
        x["row"]: x for x in client.post(f"{API}/uploads/{uid2}/dry-run", headers=h).json()["rows"]
    }
    for n in (1, 5, 6, 7):
        assert "DUP_EXACT_SAME_INST" in [o["code"] for o in rows[n]["outcomes"]], n
    assert counts() == after


def test_trust_tiers_t0_t1_t2_through_the_pipeline(client, auth):
    h = auth("INST_MAKER")
    matched = row(name="Tier Two", programme_code="UG-ECE")
    mismatch = row(name="Registry Name", programme_code="UG-ECE")
    mismatch["full_name"] = "Completely Different"  # APAAR_MISMATCH (WARNING): stays at T1
    uid = upload(client, h, to_csv([matched, mismatch]))
    client.post(f"{API}/uploads/{uid}/dry-run", headers=h)
    r = client.post(
        f"{API}/uploads/{uid}/submit", json={"justification": "Name per marksheet"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["trust_tiers"] == {"T1": 1, "T2": 1}
    with SessionLocal() as db:
        recs = {
            r.student_college_id: r
            for r in db.scalars(select(StudentRecord).where(StudentRecord.batch_id == uid))
        }
        tiers = {}
        for rec in db.scalars(select(StudentRecord).where(StudentRecord.batch_id == uid)):
            ev = db.scalars(
                select(TrustEvent)
                .where(TrustEvent.student_record_id == rec.id)
                .order_by(TrustEvent.id)
            ).all()
            tiers[rec.id] = (rec.trust_tier, [(e.from_tier, e.to_tier, e.actor) for e in ev])
    assert sorted(tiers.values()) == [
        (1, [(0, 1, "system:rule_engine")]),
        (2, [(0, 1, "system:rule_engine"), (1, 2, "system:apaar_registry")]),
    ]
    assert recs  # stored at T0 first: every event chain starts at 0


# --------------------------------------------------------------------------- maker-checker


@pytest.fixture(scope="module")
def promoted_maker(client):
    """A maker of DEMO-INST-001 who is later given the checker role (D5: the DB role is
    authoritative), to prove the self-approval check is not just the role check."""
    with SessionLocal() as db:
        inst = db.scalar(select(Institution).where(Institution.code == "DEMO-INST-001"))
        u = db.scalar(select(User).where(User.email == PROMOTED_EMAIL))
        if u is None:
            db.add(
                User(
                    email=PROMOTED_EMAIL,
                    full_name="Maker Two",
                    role=Role.INST_MAKER,
                    institution_id=inst.id,
                    password_hash=hash_password(settings.demo_password),
                )
            )
        db.commit()
    tok = login(client, PROMOTED_EMAIL).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _submitted_batch(client, h, rows) -> int:
    uid = upload(client, h, to_csv(rows))
    assert client.post(f"{API}/uploads/{uid}/dry-run", headers=h).status_code == 200
    r = client.post(f"{API}/uploads/{uid}/submit", json={"justification": "test"}, headers=h)
    assert r.status_code == 200, r.text
    return uid


def test_maker_cannot_approve_own_batch(client, auth, promoted_maker):
    uid = _submitted_batch(client, promoted_maker, [row(name="Self Approval")])
    note = {"note": "Looks fine to me"}
    # 1) a maker has no checker rights at all
    r = client.post(f"{API}/batches/{uid}/approve", json=note, headers=auth("INST_MAKER"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    # 2) even with the checker role, the batch's own maker cannot decide it
    with SessionLocal() as db:
        db.execute(update(User).where(User.email == PROMOTED_EMAIL).values(role=Role.INST_CHECKER))
        db.commit()
    try:
        for action in ("approve", "reject"):
            r = client.post(f"{API}/batches/{uid}/{action}", json=note, headers=promoted_maker)
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "MAKER_CANNOT_APPROVE_OWN_BATCH"
        detail = client.get(f"{API}/batches/{uid}", headers=promoted_maker).json()
        assert detail["is_own_batch"] is True and detail["can_decide"] is False
    finally:
        with SessionLocal() as db:
            db.execute(
                update(User).where(User.email == PROMOTED_EMAIL).values(role=Role.INST_MAKER)
            )
            db.commit()
    with SessionLocal() as db:
        assert db.get(UploadBatch, uid).status == "PENDING_APPROVAL"
    # 3) another registrar can; a decision is final
    detail = client.get(f"{API}/batches/{uid}", headers=auth("INST_CHECKER")).json()
    assert detail["can_decide"] is True
    preview = client.get(f"{API}/batches/{uid}/records", headers=auth("INST_CHECKER")).json()
    assert preview["total"] == 1 and preview["records"][0]["full_name"] == "Self Approval"
    r = client.post(f"{API}/batches/{uid}/approve", json=note, headers=auth("INST_CHECKER"))
    assert r.status_code == 200 and r.json()["activated"] == 1
    with SessionLocal() as db:
        assert {
            r.status for r in db.scalars(select(StudentRecord).where(StudentRecord.batch_id == uid))
        } == {"ACTIVE"}
        assert db.get(UploadBatch, uid).checker_id is not None
    again = client.post(f"{API}/batches/{uid}/reject", json=note, headers=auth("INST_CHECKER"))
    assert again.status_code == 409


def test_checker_approves_form_records_too(client, auth):
    body = {k: (v == "true" if k.startswith("is_") else v) for k, v in row(name="Form P").items()}
    r = client.post(f"{API}/students", json=body, headers=auth("INST_MAKER"))
    assert r.status_code == 201, r.text
    bid = r.json()["record"]["batch_id"]
    pending = client.get(f"{API}/batches?status=PENDING_APPROVAL", headers=auth("INST_CHECKER"))
    assert bid in [b["id"] for b in pending.json()]
    r = client.post(
        f"{API}/batches/{bid}/approve", json={"note": "Verified"}, headers=auth("INST_CHECKER")
    )
    assert r.status_code == 200 and r.json()["activated"] == 1


def test_rejection_releases_seats_and_updates_over_cap_case(client, auth):
    h = auth("INST_MAKER")
    nri = dict(admission_category="NRI", is_nri="true", nri_sponsor="Aunt, Muscat")
    rows = [row(name=f"Reject Nri {i}", programme_code="UG-ME", **nri) for i in range(3)]
    rows.append(row(name="Reject General", programme_code="UG-ME"))
    nri_before, gen_before = bucket_used("UG-ME", "NRI"), bucket_used("UG-ME", "APPROVED")
    uid = _submitted_batch(client, h, rows)
    assert bucket_used("UG-ME", "NRI") == nri_before + 3
    assert bucket_used("UG-ME", "APPROVED") == gen_before + 1
    with SessionLocal() as db:
        (case,) = db.scalars(
            select(Case).where(
                Case.status == CaseStatus.OPEN,
                Case.subject_ids_json["key"].astext.like("SEAT_OVER_CAP:%:NRI"),
                Case.evidence_json["programme_code"].astext == "UG-ME",
            )
        ).all()
        assert case.evidence_json["excess"] == nri_before + 3
        case_id = case.id
    for who in ("INST_MAKER", "AICTE_ADMIN"):
        r = client.post(f"{API}/batches/{uid}/reject", json={"note": "x" * 5}, headers=auth(who))
        assert r.status_code == 403
    r = client.post(
        f"{API}/batches/{uid}/reject",
        json={"note": "Wrong category: these are not NRI admissions"},
        headers=auth("INST_CHECKER"),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "REJECTED" and out["rejected"] == 4 and out["seats_released"] == 4
    nri_bucket = next(b for b in out["buckets"] if b["ledger_key"] == "NRI")
    assert nri_bucket["used_before"] - nri_bucket["used_after"] == 3
    assert bucket_used("UG-ME", "NRI") == nri_before
    assert bucket_used("UG-ME", "APPROVED") == gen_before
    with SessionLocal() as db:
        recs = db.scalars(select(StudentRecord).where(StudentRecord.batch_id == uid)).all()
        assert {r.status for r in recs} == {"REJECTED"} and not any(r.over_cap for r in recs)
        for rec in recs:
            held = db.scalar(
                select(func.sum(SeatLedgerEntry.delta)).where(
                    SeatLedgerEntry.student_record_id == rec.id
                )
            )
            assert held == 0
        case = db.get(Case, case_id)
        if nri_before == 0:
            assert case.status == "RESOLVED" and case.evidence_json["excess"] == 0
        else:
            assert case.evidence_json["excess"] == nri_before


# --------------------------------------------------------------------------- versioning (FR-4)


def test_update_versions_tier_reset_and_seat_move(client, auth):
    h = auth("INST_MAKER")
    p = row(name="Versioned Person", programme_code="UG-ECE")
    uid = _submitted_batch(client, h, [p])
    with SessionLocal() as db:
        rec = db.scalar(select(StudentRecord).where(StudentRecord.batch_id == uid))
        rid, apaar = rec.id, p["apaar_id"]
        assert rec.trust_tier == 2
    body = {k: (v == "true" if k.startswith("is_") else v) for k, v in p.items()}
    url = f"{API}/students/{apaar}/records/{rid}"
    ece, mca = bucket_used("UG-ECE", "APPROVED"), bucket_used("MCA", "APPROVED")

    # non-material edit: new version, tier unchanged
    r = client.put(url, json={**body, "phone": "9999000777", "reason": "new phone"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["material"] == []
    assert r.json()["record"]["trust_tier"] == 2 and r.json()["tier_reset"] is False
    # same values again: no new version
    r = client.put(url, json={**body, "phone": "9999000777"}, headers=h)
    assert r.json()["noop"] is True and r.json()["version"] == 2

    # material edit (DOB now differs from the registry): tier reset to T1, stays T1
    r = client.put(
        url, json={**body, "phone": "9999000777", "dob": "2007-05-02", "reason": "dob"}, headers=h
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["material"] == ["dob"] and out["tier_reset"] is True
    assert out["record"]["trust_tier"] == 1
    assert "APAAR_MISMATCH" in [o["code"] for o in out["outcomes"]]

    # material edit (programme): the seat moves under the ledger lock; DOB fixed -> T2 again
    r = client.put(url, json={**body, "programme_code": "MCA", "phone": "9999000777"}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out["material"]) == {"dob", "programme"} and out["version"] == 4
    assert out["seat_released"]["ledger_key"] == "APPROVED" and out["seat"] is not None
    assert out["record"]["trust_tier"] == 2
    assert bucket_used("UG-ECE", "APPROVED") == ece - 1
    assert bucket_used("MCA", "APPROVED") == mca + 1

    with SessionLocal() as db:
        versions = db.scalars(
            select(StudentRecordVersion)
            .where(StudentRecordVersion.student_record_id == rid)
            .order_by(StudentRecordVersion.version)
        ).all()
        assert [v.version for v in versions] == [1, 2, 3, 4]  # append-only
        assert versions[2].data_json["material_fields"] == ["dob"]
        ev = db.scalars(
            select(TrustEvent).where(TrustEvent.student_record_id == rid).order_by(TrustEvent.id)
        ).all()
        assert [(e.from_tier, e.to_tier) for e in ev] == [(0, 1), (1, 2), (2, 1), (1, 2)]
        assert ev[2].actor.startswith("user:") and "Material edit" in ev[2].reason

    # the APAAR ID is the person key: it cannot be changed by an update
    r = client.put(url, json={**body, "apaar_id": "961234567890"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "APAAR_CHANGE_NOT_ALLOWED"
    # other institutions cannot see or edit it
    assert client.put(url, json=body, headers=auth("OTHER_MAKER")).status_code == 404
    assert client.get(f"{API}/students/{apaar}", headers=auth("OTHER_MAKER")).status_code == 404
    got = client.get(f"{API}/students/{apaar}", headers=auth("INST_CHECKER")).json()
    assert len(got["records"][0]["versions"]) == 4
    # an edit that introduces an ERROR is refused and changes nothing
    r = client.put(url, json={**body, "programme_code": "NOPE"}, headers=h)
    assert r.status_code == 422
    with SessionLocal() as db:
        assert db.get(StudentRecord, rid).current_version == 4


# --------------------------------------------------------------------------- dashboard


def test_institution_dashboard_slice(client, auth, inst_ids):
    own = inst_ids["DEMO-INST-001"]
    r = client.get(f"{API}/dashboards/institution/{own}", headers=auth("INST_CHECKER"))
    assert r.status_code == 200, r.text
    d = r.json()
    totals = {t["ledger_key"]: t for t in d["seats"]["totals"]}
    assert totals["APPROVED"]["cap"] == 120 + 60 + 60 + 60 + 60
    assert totals["NRI"]["cap"] == 0 and totals["NRI"]["used"] == totals["NRI"]["excess"]
    tiers = d["trust_tiers"]["by_tier"]
    assert tiers["T2"] >= 1 and tiers["T3"] == 0
    assert sum(tiers.values()) == d["trust_tiers"]["total"]
    assert d["submissions"]["by_status"]["REJECTED"] >= 1
    assert d["recent_errors"] and all(e["code"] for e in d["recent_errors"])
    # institution users never receive AICTE_ONLY cases; AICTE users do
    assert all(c["visibility"] == "INSTITUTION" for c in d["pending_cases"])
    aicte = client.get(f"{API}/dashboards/institution/{own}", headers=auth("AICTE_REVIEWER"))
    assert any(c["type"] == "SEAT_OVER_CAP" for c in aicte.json()["pending_cases"])
    other = inst_ids["DEMO-INST-002"]
    assert (
        client.get(f"{API}/dashboards/institution/{other}", headers=auth("INST_MAKER")).status_code
        == 404
    )
    tt = client.get(f"{API}/dashboards/trust-tiers", headers=auth("INST_MAKER")).json()
    assert tt["institution_id"] == own and tt["by_tier"] == tiers
    assert client.get(f"{API}/dashboards/trust-tiers", headers=auth("STUDENT")).status_code == 403
