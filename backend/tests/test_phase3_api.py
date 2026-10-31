"""Phase 3 API: POST /validate/record, POST /students (flag mode), cases visibility,
FR-11 decisions, GET/PUT /rules, GET /institutions/{id}/seats."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete, func, select, update

from app.db.models import (
    Case,
    Institution,
    Notification,
    QuotaRule,
    RecordStatus,
    SeatLedgerEntry,
    StudentRecord,
    StudentRecordVersion,
    TrustEvent,
    User,
    ValidationResult,
)
from app.db.session import SessionLocal
from app.services import seat_ledger
from app.synth.loader import reset_generated_data
from tests.conftest import API, OTHER_MAKER_EMAIL
from tests.factories import drop_test_institutions, register


@pytest.fixture(scope="module", autouse=True)
def _clean_state():
    with SessionLocal() as db:
        reset_generated_data(db)
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(delete(QuotaRule))
        drop_test_institutions(db)
        reset_generated_data(db)
        db.commit()


@pytest.fixture(scope="module")
def inst_ids():
    with SessionLocal() as db:
        return {i.code: i.id for i in db.scalars(select(Institution))}


def person(name="Kavya Iyer", dob=date(2007, 4, 2), gender="F", **kw) -> dict:
    with SessionLocal() as db:
        apaar = register(db, name, dob, gender)
        db.commit()
    body = dict(
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
        phone="9999000001",
        email="test.student@mail.example",
        address="1 Test Road, Pune",
    )
    body.update(kw)
    return body


def _counts():
    with SessionLocal() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(m))
            for m in (StudentRecord, ValidationResult, Case, SeatLedgerEntry)
        )


def _codes(body) -> list[str]:
    return [o["code"] for o in body["outcomes"]]


# --------------------------------------------------------------------------- validate


def test_validate_clean_record_with_apaar_status_and_projection(client, auth):
    before = _counts()
    r = client.post(f"{API}/validate/record", json=person(), headers=auth("INST_MAKER"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcomes"] == [] and body["has_error"] is False
    assert body["apaar_status"] == {
        "source": "MOCK",
        "format_ok": True,
        "suspicious": False,
        "found": True,
        "name_match": True,
        "dob_match": True,
    }
    assert body["programme"]["programme_code"] == "UG-CSE"
    proj = body["seat_projection"]
    assert proj["ledger_key"] == "APPROVED" and proj["cap"] == 120
    assert proj["used_after"] == proj["used"] + 1 and proj["would_be_flagged"] is False
    assert _counts() == before  # dry run: nothing written


def test_validate_reports_errors_and_never_returns_registry_data(client, auth):
    body = person(name="Meera Pillai")
    body["full_name"] = "Totally Different"
    body["dob"] = "2006-01-01"
    r = client.post(f"{API}/validate/record", json=body, headers=auth("INST_MAKER"))
    out = r.json()
    assert "APAAR_MISMATCH" in _codes(out)
    assert "Meera" not in r.text and "2007-04-02" not in r.text
    bad = person(apaar_id="12345", programme_code="ug-ece-x")
    out = client.post(f"{API}/validate/record", json=bad, headers=auth("INST_MAKER")).json()
    assert {"APAAR_INVALID", "PROGRAMME_NOT_APPROVED"} <= set(_codes(out))
    assert out["has_error"] is True and out["seat_projection"] is None
    unknown = person(apaar_id="961111111110")
    out = client.post(f"{API}/validate/record", json=unknown, headers=auth("INST_MAKER")).json()
    assert "APAAR_NOT_FOUND" in _codes(out) and out["apaar_status"]["found"] is False


def test_validate_rbac_and_institution_scoping(client, auth, inst_ids):
    url = f"{API}/validate/record"
    for who in ("VERIFIER", "STUDENT"):
        assert client.post(url, json=person(), headers=auth(who)).status_code == 403
    assert client.post(url, json=person()).status_code == 401
    other = person(institution_id=inst_ids["DEMO-INST-002"])
    assert client.post(url, json=other, headers=auth("INST_MAKER")).status_code == 404
    r = client.post(url, json=person(), headers=auth("AICTE_ADMIN"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "INSTITUTION_REQUIRED"
    own = person(institution_id=inst_ids["DEMO-INST-001"])
    assert client.post(url, json=own, headers=auth("AICTE_REVIEWER")).status_code == 200
    assert client.get(f"{API}/validate/codes", headers=auth("INST_MAKER")).status_code == 200


# --------------------------------------------------------------------------- create


def test_create_clean_record_is_pending_t1_and_takes_a_seat(client, auth):
    r = client.post(
        f"{API}/students", json=person(name="Arjun Menon", gender="M"), headers=auth("INST_MAKER")
    )
    assert r.status_code == 201, r.text
    rec = r.json()["record"]
    # Phase 4: a registry match (name + DOB) lifts the record on to T2 (FR-18).
    assert rec["status"] == "PENDING_APPROVAL" and rec["trust_tier"] == 2
    assert rec["ledger_key"] == "APPROVED" and rec["over_cap"] is False
    assert rec["batch_id"] is not None  # its own one-row FORM batch, awaiting the checker
    with SessionLocal() as db:
        ev = db.scalars(select(TrustEvent).where(TrustEvent.student_record_id == rec["id"])).all()
        assert [(e.from_tier, e.to_tier) for e in ev] == [(0, 1), (1, 2)]
        assert (
            db.scalar(
                select(func.count()).where(StudentRecordVersion.student_record_id == rec["id"])
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.sum(SeatLedgerEntry.delta)).where(
                    SeatLedgerEntry.student_record_id == rec["id"]
                )
            )
            == 1
        )


def test_create_with_errors_is_refused_and_stores_nothing(client, auth):
    before = _counts()
    r = client.post(
        f"{API}/students", json=person(apaar_id="961111111110"), headers=auth("INST_MAKER")
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "RECORD_HAS_ERRORS"
    assert "APAAR_NOT_FOUND" in [o["code"] for o in err["details"]["outcomes"]]
    assert _counts() == before


def test_create_is_maker_only(client, auth):
    for who in ("INST_CHECKER", "AICTE_ADMIN", "VERIFIER", "STUDENT"):
        assert client.post(f"{API}/students", json=person(), headers=auth(who)).status_code == 403


def test_flag_not_reject_over_cap_record_is_stored_and_case_is_aicte_only(client, auth):
    body = person(
        name="Rohan Das",
        gender="M",
        programme_code="UG-ME",
        admission_category="NRI",
        is_nri=True,
        nri_sponsor="Father, Dubai",
    )
    r = client.post(f"{API}/students", json=body, headers=auth("INST_MAKER"))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["record"]["over_cap"] is True and out["record"]["status"] == "PENDING_APPROVAL"
    warn = next(o for o in out["outcomes"] if o["code"] == "SEAT_OVER_CAP")
    assert warn["severity"] == "WARNING"
    assert warn["evidence"] == {"bucket": "NRI", "cap": 0, "used": 1, "excess": 1}
    assert "held for review" in warn["message_en"] and "held for review" in out["message_en"]
    assert "reject" not in r.text.lower()

    cases = client.get(f"{API}/cases?type=SEAT_OVER_CAP", headers=auth("AICTE_REVIEWER")).json()
    mine = [c for c in cases if out["record"]["id"] in c["subject_ids_json"]["record_ids"]]
    assert len(mine) == 1 and mine[0]["visibility"] == "AICTE_ONLY"
    assert mine[0]["evidence_json"]["excess"] == 1
    for who in ("INST_MAKER", "INST_CHECKER", "OTHER_MAKER"):
        assert client.get(f"{API}/cases", headers=auth(who)).json() == []
        assert client.get(f"{API}/cases/{mine[0]['id']}", headers=auth(who)).status_code == 404
    assert client.get(f"{API}/cases", headers=auth("VERIFIER")).status_code == 403


def test_cross_institution_duplicate_neutral_message_and_aicte_case(client, auth):
    p = person(name="Sneha Kulkarni", dob=date(2003, 6, 9))
    first = client.post(f"{API}/students", json=p, headers=auth("INST_MAKER"))
    assert first.status_code == 201, first.text
    second = client.post(
        f"{API}/students",
        json={**p, "programme_code": "PG-MBA", "admission_date": "2025-08-20"},
        headers=auth("OTHER_MAKER"),
    )
    assert second.status_code == 201, second.text
    assert _codes(second.json()) == ["ENROLMENT_ELSEWHERE_NEUTRAL"]
    neutral = second.json()["outcomes"][0]
    assert neutral["severity"] == "INFO" and neutral["evidence"] == {}
    for leak in ("DEMO-INST-001", "Demo Institute", "UG-CSE", "2025-08-05"):
        assert leak not in second.text, leak
    ids = {first.json()["record"]["id"], second.json()["record"]["id"]}
    cases = client.get(f"{API}/cases?type=DUP_EXACT_CROSS", headers=auth("AICTE_ADMIN")).json()
    (case,) = [c for c in cases if set(c["subject_ids_json"]["record_ids"]) == ids]
    assert case["visibility"] == "AICTE_ONLY" and case["evidence_json"]["institutions"] == 2
    assert p["apaar_id"] not in case["subject_ids_json"]["key"]
    for who in ("INST_MAKER", "OTHER_MAKER"):
        assert client.get(f"{API}/cases/{case['id']}", headers=auth(who)).status_code == 404
    # same student again at the same institution and programme: exact duplicate, refused
    again = client.post(f"{API}/students", json=p, headers=auth("INST_MAKER"))
    assert again.status_code == 422
    assert "DUP_EXACT_SAME_INST" in [
        o["code"] for o in again.json()["error"]["details"]["outcomes"]
    ]


def test_genuine_transfer_gives_info_and_no_case(client, auth):
    p = person(
        name="Imran Qureshi",
        dob=date(2003, 2, 11),
        gender="M",
        programme_code="UG-ECE",
        admission_date="2025-07-10",
    )
    first = client.post(f"{API}/students", json=p, headers=auth("INST_MAKER")).json()
    with SessionLocal() as db:  # the student left institution A (status changes are Phase 4)
        db.execute(
            update(StudentRecord)
            .where(StudentRecord.id == first["record"]["id"])
            .values(status=RecordStatus.WITHDRAWN, end_date=date(2025, 7, 30))
        )
        db.commit()
    moved = client.post(
        f"{API}/students",
        json={**p, "programme_code": "PG-MBA", "admission_date": "2025-08-10"},
        headers=auth("OTHER_MAKER"),
    )
    assert moved.status_code == 201, moved.text
    assert _codes(moved.json()) == ["TRANSFER_DETECTED"]
    ids = {first["record"]["id"], moved.json()["record"]["id"]}
    for c in client.get(f"{API}/cases", headers=auth("AICTE_ADMIN")).json():
        assert not ids & set(c["subject_ids_json"].get("record_ids", [])), c


# --------------------------------------------------------------------------- FR-11


def _nri_records(client, auth, n, programme="UG-ECE"):
    ids = []
    for i in range(n):
        body = person(
            name=f"Nri Student {i}",
            programme_code=programme,
            admission_category="NRI",
            is_nri=True,
            nri_sponsor="Uncle, Oman",
        )
        r = client.post(f"{API}/students", json=body, headers=auth("INST_MAKER"))
        assert r.status_code == 201, r.text
        ids.append(r.json()["record"]["id"])
    return ids


def _case_for(client, auth, rid):
    for c in client.get(f"{API}/cases?type=SEAT_OVER_CAP", headers=auth("AICTE_ADMIN")).json():
        if rid in c["subject_ids_json"]["record_ids"]:
            return c
    return None


def test_fr11_request_correction_then_accept_exception(client, auth):
    a, b = _nri_records(client, auth, 2)
    case = _case_for(client, auth, a)
    assert case["evidence_json"]["excess"] == 2 and case["status"] == "OPEN"
    url = f"{API}/cases/{case['id']}/decision"
    body = {"action": "REQUEST_CORRECTION", "note": "NRI seats not approved", "record_ids": [a]}
    assert client.post(url, json=body, headers=auth("INST_CHECKER")).status_code == 403
    assert (
        client.post(
            url, json={**body, "record_ids": [999999]}, headers=auth("AICTE_ADMIN")
        ).status_code
        == 422
    )
    r = client.post(url, json=body, headers=auth("AICTE_REVIEWER"))
    assert r.status_code == 200, r.text
    assert r.json()["released"] == [a] and r.json()["case_status"] == "OPEN"
    assert r.json()["case"]["evidence_json"]["excess"] == 1
    with SessionLocal() as db:
        rec = db.get(StudentRecord, a)
        assert rec.status == "NEEDS_CORRECTION" and rec.over_cap is False
        assert (
            db.scalar(
                select(func.sum(SeatLedgerEntry.delta)).where(
                    SeatLedgerEntry.student_record_id == a
                )
            )
            == 0
        )
        notified = db.execute(
            select(User.email, Notification.payload_json)
            .join(User, User.id == Notification.user_id)
            .where(Notification.kind == "SEAT_CORRECTION_REQUESTED")
        ).all()
    assert {"maker@inst1.demo", "checker@inst1.demo"} <= {e for e, _ in notified}
    assert OTHER_MAKER_EMAIL not in {e for e, _ in notified}
    assert all(a in p["record_ids"] for _, p in notified)

    accept = {"action": "ACCEPT_EXCEPTION", "note": "Approved by the bureau for 2025-26"}
    r = client.post(url, json=accept, headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200 and r.json()["case_status"] == "RESOLVED"
    assert r.json()["case"]["decision_note"] == accept["note"]
    with SessionLocal() as db:
        rec = db.get(StudentRecord, b)
        assert rec.over_cap is True and rec.over_cap_reviewed is True
    assert client.post(url, json=accept, headers=auth("AICTE_ADMIN")).status_code == 409


def test_decisions_on_other_case_types_wait_for_phase5(client, auth):
    cases = client.get(f"{API}/cases?type=DUP_EXACT_CROSS", headers=auth("AICTE_ADMIN")).json()
    assert cases
    r = client.post(
        f"{API}/cases/{cases[0]['id']}/decision",
        json={"action": "ACCEPT_EXCEPTION", "note": "n/a"},
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "CASE_TYPE_UNSUPPORTED"


# --------------------------------------------------------------------------- rules


def test_rules_endpoints_are_admin_only(client, auth):
    for who in ("AICTE_REVIEWER", "INST_MAKER", "INST_CHECKER", "VERIFIER", "STUDENT"):
        assert client.get(f"{API}/rules", headers=auth(who)).status_code == 403
        assert (
            client.put(
                f"{API}/rules/bucket.LATERAL", json={"value_json": {"pct": 0.2}}, headers=auth(who)
            ).status_code
            == 403
        )
    rules = {r["key"]: r for r in client.get(f"{API}/rules", headers=auth("AICTE_ADMIN")).json()}
    assert rules["bucket.SUPERNUMERARY_FN_OCI_PIO_CIWGC"]["verify"] is True
    assert rules["bucket.LATERAL"]["active_value"]["pct"] == 0.20
    assert "validation.name_match" in rules


def test_rule_validation(client, auth):
    h = auth("AICTE_ADMIN")
    assert client.put(f"{API}/rules/nope", json={"value_json": {}}, headers=h).status_code == 404
    r = client.put(f"{API}/rules/bucket.LATERAL", json={"value_json": {"pct": 2}}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "RULE_INVALID"


def test_put_rule_versions_and_recomputes_caps(client, auth):
    """Rules-as-data: approving NRI seats for one programme row un-flags its admission and
    resolves the over-cap case, with no code change."""
    (rid,) = _nri_records(client, auth, 1, programme="MCA")
    case = _case_for(client, auth, rid)
    assert case["status"] == "OPEN"
    value = {"default": 0, "by_institution_programme": {"DEMO-INST-001|MCA|2025-26|REGULAR": 1}}
    r = client.put(
        f"{API}/rules/bucket.NRI",
        json={"value_json": value, "description": "NRI seats approved"},
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["recompute"]["caps_changed"] >= 1
    with SessionLocal() as db:
        assert db.get(StudentRecord, rid).over_cap is False
        assert db.get(Case, case["id"]).status == "RESOLVED"
    r = client.put(
        f"{API}/rules/bucket.NRI", json={"value_json": {"default": 0}}, headers=auth("AICTE_ADMIN")
    )
    assert r.json()["version"] == 2
    with SessionLocal() as db:
        assert db.get(StudentRecord, rid).over_cap is True  # cap back to 0: flagged again
        assert (
            seat_ledger.open_over_cap_case(
                db, db.get(StudentRecord, rid).institution_programme_id, "NRI"
            )
            is not None
        )
    rules = {x["key"]: x for x in client.get(f"{API}/rules", headers=auth("AICTE_ADMIN")).json()}
    assert rules["bucket.NRI"]["active_version"] == 2
    assert [v["version"] for v in rules["bucket.NRI"]["versions"]] == [2, 1]


# --------------------------------------------------------------------------- seats


def test_seats_endpoint_is_scoped(client, auth, inst_ids):
    own = inst_ids["DEMO-INST-001"]
    r = client.get(f"{API}/institutions/{own}/seats", headers=auth("INST_MAKER"))
    assert r.status_code == 200
    rows = {(x["programme_code"], x["shift"]): x for x in r.json()}
    cse = rows[("UG-CSE", "REGULAR")]
    buckets = {b["ledger_key"]: b for b in cse["buckets"]}
    assert set(buckets) == {
        "APPROVED",
        "SUPERNUMERARY_FN_OCI_PIO_CIWGC",
        "SUPERNUMERARY_TFW",
        "NRI",
        "LATERAL",
    }
    assert buckets["APPROVED"]["cap"] == 120 and buckets["LATERAL"]["cap"] == 24
    assert buckets["APPROVED"]["used"] >= 1 and cse["records_received"] >= 1
    other = inst_ids["DEMO-INST-002"]
    assert (
        client.get(f"{API}/institutions/{other}/seats", headers=auth("INST_MAKER")).status_code
        == 404
    )
    assert (
        client.get(f"{API}/institutions/{other}/seats", headers=auth("AICTE_REVIEWER")).status_code
        == 200
    )
    assert (
        client.get(f"{API}/institutions/{own}/seats", headers=auth("VERIFIER")).status_code == 403
    )
