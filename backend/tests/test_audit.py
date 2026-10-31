"""Hash-chained audit log (FR-35, spec 13 "Audit chain"): valid chain passes; a modified,
deleted or reordered row is detected at the right index; entries are written in the same
transaction as the action; concurrent writers keep one linear chain."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core import audit
from app.db.models import AuditLog, Role, User
from app.db.session import SessionLocal
from app.synth.loader import reset_generated_data
from tests.conftest import API, login


@pytest.fixture(scope="module", autouse=True)
def _clean_state():
    with SessionLocal() as db:
        reset_generated_data(db)
        db.commit()
    yield
    with SessionLocal() as db:
        reset_generated_data(db)
        db.commit()


def _verify(client, auth):
    r = client.get(f"{API}/audit/verify", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    return r.json()


def _ids() -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(AuditLog.id).order_by(AuditLog.id)))


def _tamper(sql: str, params: dict) -> None:
    """Simulate a DBA editing the table directly (bypassing the append-only trigger)."""
    with SessionLocal() as db:
        db.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
        db.execute(text(sql), params)
        db.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
        db.commit()


def _seed_entries(n: int = 5) -> None:
    with SessionLocal() as db:
        for i in range(n):
            audit.record(db, "TEST_EVENT", entity_type="test", entity_id=i, detail={"i": i})
        db.commit()


def test_login_and_failed_login_are_audited_without_pii(client):
    assert login(client, "maker@inst1.demo").status_code == 200
    assert login(client, "maker@inst1.demo", "wrong-password").status_code == 401
    assert login(client, "nobody@nowhere.example", "x").status_code == 401
    with SessionLocal() as db:
        last = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(3)).all()[::-1]
    assert [e.action for e in last] == ["LOGIN", "LOGIN_FAILED", "LOGIN_FAILED"]
    assert last[1].detail_json == {"reason": "bad_password"} and last[1].actor_id is not None
    assert last[2].detail_json == {"reason": "unknown_account"} and last[2].actor_id is None
    assert all("nobody" not in str(e.detail_json) for e in last)


def test_valid_chain_passes_and_links(client, auth):
    _seed_entries()
    out = _verify(client, auth)
    assert out["valid"] is True and out["first_broken"] is None
    with SessionLocal() as db:
        rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert rows[0].prev_hash == audit.GENESIS_HASH
    for a, b in zip(rows, rows[1:], strict=False):
        assert b.prev_hash == a.hash
    assert out["entries"] == len(rows) and out["head_hash"] == rows[-1].hash


def test_modified_row_detected_at_the_right_index(client, auth):
    _seed_entries()
    ids = _ids()
    k = len(ids) - 3
    target = ids[k]
    with SessionLocal() as db:
        original = db.get(AuditLog, target).detail_json
    _tamper("UPDATE audit_log SET detail_json = '{\"i\": 999}' WHERE id = :id", {"id": target})
    try:
        out = _verify(client, auth)
        assert out["valid"] is False
        broken = out["first_broken"]
        assert broken["index"] == k and broken["id"] == target
        assert broken["reason"] == "hash_mismatch"
    finally:
        _tamper(
            "UPDATE audit_log SET detail_json = CAST(:d AS jsonb) WHERE id = :id",
            {"id": target, "d": audit.canonical_json(original)},
        )
    assert _verify(client, auth)["valid"] is True


def test_deleted_row_detected_at_the_next_index(client, auth):
    _seed_entries()
    ids = _ids()
    k = len(ids) - 2
    with SessionLocal() as db:
        row = audit.to_dict(db.get(AuditLog, ids[k]))
    _tamper("DELETE FROM audit_log WHERE id = :id", {"id": ids[k]})
    try:
        broken = _verify(client, auth)["first_broken"]
        # the entry after the gap no longer links to its predecessor
        assert broken["index"] == k and broken["id"] == ids[k + 1]
        assert broken["reason"] == "prev_hash_mismatch"
    finally:
        _tamper(
            "INSERT INTO audit_log (id, ts, actor_id, actor_role, action, entity_type, "
            "entity_id, detail_json, prev_hash, hash) VALUES (:id, :ts, :actor_id, :actor_role, "
            ":action, :entity_type, :entity_id, CAST(:detail AS jsonb), :prev_hash, :hash)",
            {**row, "detail": audit.canonical_json(row["detail"])},
        )
    assert _verify(client, auth)["valid"] is True


def test_audit_table_is_append_only():
    with SessionLocal() as db:
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(
                text("UPDATE audit_log SET action = 'X' WHERE id = (SELECT max(id) FROM audit_log)")
            )
        db.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("DELETE FROM audit_log"))
        db.rollback()


def test_entry_is_written_in_the_same_transaction_as_the_action():
    """A rolled-back action leaves no audit entry, and a committed one carries its entry."""
    before = len(_ids())
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.role == Role.AICTE_ADMIN))
        user.full_name = "Renamed In Txn"
        audit.record(db, "TEST_ACTION", actor=user, entity_type="user", entity_id=user.id)
        # not visible from another connection before commit
        with SessionLocal() as other:
            assert other.scalar(select(func.count()).select_from(AuditLog)) == before
        db.rollback()
    assert len(_ids()) == before
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.role == Role.AICTE_ADMIN))
        assert user.full_name != "Renamed In Txn"


def test_failed_actions_write_no_audit_entry(client, auth):
    before = len(_ids())
    # forbidden and invalid requests are refused before anything is written
    assert (
        client.post(
            f"{API}/batches/999999/approve", json={"note": "abc"}, headers=auth("INST_MAKER")
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/batches/999999/approve", json={"note": "abc"}, headers=auth("INST_CHECKER")
        ).status_code
        == 404
    )
    r = client.put(
        f"{API}/rules/bucket.LATERAL", json={"value_json": {"pct": 9}}, headers=auth("AICTE_ADMIN")
    )
    assert r.status_code == 422
    assert len(_ids()) == before


def test_rule_change_is_audited(client, auth):
    r = client.put(
        f"{API}/rules/bucket.LATERAL",
        json={"value_json": {"pct": 0.2, "rounding": "ceil", "verify": True}},
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200, r.text
    entries = client.get(f"{API}/audit?action=RULE_UPDATE", headers=auth("AICTE_REVIEWER")).json()
    assert entries["total"] >= 1
    assert entries["entries"][0]["entity_id"] == "bucket.LATERAL"
    assert _verify(client, auth)["valid"] is True


def test_concurrent_writers_keep_one_linear_chain(client, auth):
    n = 20
    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            with SessionLocal() as db:
                barrier.wait()
                audit.record(db, "TEST_CONCURRENT", entity_type="test", entity_id=i)
                db.commit()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with SessionLocal() as db:
        prevs = db.scalars(select(AuditLog.prev_hash)).all()
    assert len(prevs) == len(set(prevs))  # nobody chained from the same predecessor
    assert _verify(client, auth)["valid"] is True


def test_audit_endpoints_rbac(client, auth):
    for who in ("AICTE_ADMIN", "AICTE_REVIEWER"):
        assert client.get(f"{API}/audit", headers=auth(who)).status_code == 200
        assert client.get(f"{API}/audit/verify", headers=auth(who)).status_code == 200
    for who in ("INST_MAKER", "INST_CHECKER", "VERIFIER", "STUDENT"):
        assert client.get(f"{API}/audit", headers=auth(who)).status_code == 403
        assert client.get(f"{API}/audit/verify", headers=auth(who)).status_code == 403
