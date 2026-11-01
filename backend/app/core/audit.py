"""Hash-chained, append-only audit log (FR-35, spec 5.8 and 12; P20).

Every entry stores `prev_hash` and

    hash = SHA256(prev_hash || canonical_json(entry))

where `entry` is the row's own content (id, ts, actor, action, entity, detail) and
canonical JSON means sorted keys, no whitespace, UTF-8. The first entry chains from
`GENESIS_HASH` (64 zeros).

Rules:
- `record()` is called inside the transaction of the action it describes and never
  commits: the caller's commit makes the action and its audit entry durable together, and a
  rollback discards both.
- Chain ordering is serialised with a transaction-scoped Postgres advisory lock
  (`pg_advisory_xact_lock`): the writer takes the lock, reads the current head, reserves the
  next id and inserts. The lock is held until the writer's transaction ends, so concurrent
  writers can never chain from the same `prev_hash`. Call `record()` as the last step
  before commit to keep that window short.
- `verify()` recomputes the chain in id order and reports the first broken entry.
- Details carry ids and counts only, never raw PII (FR-38).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import AuditLog, User

GENESIS_HASH = "0" * 64
# Arbitrary constant key for the chain lock ("AUDI").
AUDIT_LOCK_KEY = 0x41554449


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def entry_dict(
    *,
    id: int,
    ts: datetime,
    actor_id: int | None,
    actor_role: str | None,
    action: str,
    entity_type: str | None,
    entity_id: str | None,
    detail: Any,
) -> dict[str, Any]:
    return {
        "id": id,
        "ts": _ts(ts),
        "actor_id": actor_id,
        "actor_role": actor_role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "detail": detail,
    }


def compute_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(entry)).encode("utf-8")).hexdigest()


def _row_entry(row: AuditLog) -> dict[str, Any]:
    return entry_dict(
        id=row.id,
        ts=row.ts,
        actor_id=row.actor_id,
        actor_role=row.actor_role,
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        detail=row.detail_json,
    )


def record(
    db: Session,
    action: str,
    *,
    actor: User | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one entry to the chain inside the caller's transaction. Does not commit."""
    # Normalise the detail exactly as it will round-trip through JSONB.
    detail_norm = json.loads(canonical_json(detail)) if detail is not None else None
    actor_id = actor.id if actor is not None else None
    role = actor_role or (str(actor.role) if actor is not None else None)
    eid = str(entity_id) if entity_id is not None else None

    db.execute(select(func.pg_advisory_xact_lock(AUDIT_LOCK_KEY)))
    prev = db.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)) or GENESIS_HASH
    new_id = db.scalar(text("SELECT nextval(pg_get_serial_sequence('audit_log', 'id'))"))
    ts = datetime.now(UTC)
    entry = entry_dict(
        id=new_id,
        ts=ts,
        actor_id=actor_id,
        actor_role=role,
        action=action,
        entity_type=entity_type,
        entity_id=eid,
        detail=detail_norm,
    )
    row = AuditLog(
        id=new_id,
        ts=ts,
        actor_id=actor_id,
        actor_role=role,
        action=action,
        entity_type=entity_type,
        entity_id=eid,
        detail_json=detail_norm,
        prev_hash=prev,
        hash=compute_hash(prev, entry),
    )
    db.add(row)
    db.flush()
    return row


def verify(db: Session) -> dict[str, Any]:
    """Recompute the whole chain (id order). Returns the first broken entry, if any:
    `index` is its 0-based position in the chain, `id` its row id, and `reason` either
    `prev_hash_mismatch` (an entry was removed, inserted or reordered before it) or
    `hash_mismatch` (its content was changed)."""
    prev = GENESIS_HASH
    count = 0
    stmt = select(AuditLog).order_by(AuditLog.id)
    rows = list(db.scalars(stmt).all())
    for index, row in enumerate(rows):
        count += 1
        if row.prev_hash != prev:
            return _broken(index, row, "prev_hash_mismatch", prev, count)
        expected = compute_hash(prev, _row_entry(row))
        if row.hash != expected:
            return _broken(index, row, "hash_mismatch", expected, count)
        prev = row.hash
    return {
        "valid": True,
        "entries": count,
        "head_hash": prev if count else None,
        "first_broken": None,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _broken(index: int, row: AuditLog, reason: str, expected: str, count: int) -> dict[str, Any]:
    return {
        "valid": False,
        "entries_checked": count,
        "first_broken": {
            "index": index,
            "id": row.id,
            "action": row.action,
            "reason": reason,
            "expected": expected,
            "stored_prev_hash": row.prev_hash,
            "stored_hash": row.hash,
        },
        "checked_at": datetime.now(UTC).isoformat(),
    }


def to_dict(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "ts": _ts(row.ts),
        "actor_id": row.actor_id,
        "actor_role": row.actor_role,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "detail": row.detail_json,
        "prev_hash": row.prev_hash,
        "hash": row.hash,
    }
