"""Trust tiers (FR-18, spec 5.3).

- T0 submitted by the institution (on ingest)
- T1 passed the deterministic rule checks with no ERROR
- T2 the APAAR ID matched the (MOCK) registry adapter: name (fuzzy) and DOB (exact)
- T3 confirmed by the student (Phase 7; not implemented here)

Tiers only rise by their own conditions; a material edit (name, DOB, programme, bucket)
resets the tier to T1 and the checks are re-run (FR-4). Every change is logged in
`trust_events` (who, why, when).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, update
from sqlalchemy.orm import Session

from app.db.models import StudentRecord, TrustEvent

T0, T1, T2, T3 = 0, 1, 2, 3
REASON_T1 = "Passed deterministic rule checks with no ERROR (FR-18 T1)"
REASON_T2 = (
    "APAAR ID matched the registry adapter (MOCK): name fuzzy match and exact DOB (FR-18 T2)"
)
REASON_RESET = "Material edit ({fields}): tier reset to T1 and checks re-run (FR-4, FR-18)"

ACTOR_RULES = "system:rule_engine"
ACTOR_REGISTRY = "system:apaar_registry"


def registry_matched(apaar_status: dict[str, Any] | None) -> bool:
    """The T2 condition: the APAAR ID is well-formed, present in the registry, and both the
    name (fuzzy) and DOB (exact) match it."""
    s = apaar_status or {}
    return bool(
        s.get("format_ok")
        and s.get("found") is True
        and s.get("name_match") is True
        and s.get("dob_match") is True
    )


def earned_tier(has_error: bool, apaar_status: dict[str, Any] | None) -> int:
    """Highest tier a record earns from its rule report (T3 is never earned here)."""
    if has_error:
        return T0
    return T2 if registry_matched(apaar_status) else T1


def _event(record_id: int, frm: int, to: int, reason: str, actor: str) -> dict[str, Any]:
    return dict(student_record_id=record_id, from_tier=frm, to_tier=to, reason=reason, actor=actor)


def rise_events(record_id: int, current: int, target: int, actor_t1: str = ACTOR_RULES):
    """Trust events for climbing from `current` to `target` one tier at a time."""
    events = []
    if current < T1 <= target:
        events.append(_event(record_id, current, T1, REASON_T1, actor_t1))
        current = T1
    if current < T2 <= target:
        events.append(_event(record_id, current, T2, REASON_T2, ACTOR_REGISTRY))
    return events


def raise_tier(db: Session, record: StudentRecord, to_tier: int, reason: str, actor: str) -> bool:
    """Raise `record` to `to_tier` if that is higher than its tier. Does not commit."""
    if record.trust_tier >= to_tier:
        return False
    db.add(
        TrustEvent(
            student_record_id=record.id,
            from_tier=record.trust_tier,
            to_tier=to_tier,
            reason=reason,
            actor=actor,
        )
    )
    db.execute(
        update(StudentRecord).where(StudentRecord.id == record.id).values(trust_tier=to_tier)
    )
    record.trust_tier = to_tier
    return True


def apply_earned(
    db: Session,
    record: StudentRecord,
    has_error: bool,
    apaar_status: dict[str, Any] | None,
    actor_t1: str = ACTOR_RULES,
) -> list[int]:
    """Raise one record to the tier its checks earn (T0 -> T1 -> T2), logging each step.
    Never lowers a tier. Returns the tiers reached."""
    target = earned_tier(has_error, apaar_status)
    reached = []
    for ev in rise_events(record.id, record.trust_tier, target, actor_t1):
        db.add(TrustEvent(**ev))
        reached.append(ev["to_tier"])
    if reached:
        db.execute(
            update(StudentRecord).where(StudentRecord.id == record.id).values(trust_tier=target)
        )
        record.trust_tier = target
    return reached


def reset_for_material_edit(
    db: Session, record: StudentRecord, fields: list[str], actor: str
) -> bool:
    """A material edit puts a record above T1 back to T1 (FR-4). Does not commit."""
    if record.trust_tier <= T1:
        return False
    db.add(
        TrustEvent(
            student_record_id=record.id,
            from_tier=record.trust_tier,
            to_tier=T1,
            reason=REASON_RESET.format(fields=", ".join(fields)),
            actor=actor,
        )
    )
    db.execute(update(StudentRecord).where(StudentRecord.id == record.id).values(trust_tier=T1))
    record.trust_tier = T1
    return True


def bulk_apply(
    db: Session, items: list[tuple[int, int]], actor_t1: str = ACTOR_RULES, chunk: int = 5000
) -> dict[int, int]:
    """Bulk version for freshly stored T0 records: `items` = (record_id, target tier).
    Returns {tier: count}."""
    events: list[dict[str, Any]] = []
    by_target: dict[int, list[int]] = {}
    for rid, target in items:
        events.extend(rise_events(rid, T0, target, actor_t1))
        by_target.setdefault(target, []).append(rid)
    for target, ids in by_target.items():
        if target == T0:
            continue
        for i in range(0, len(ids), chunk):
            db.execute(
                update(StudentRecord)
                .where(StudentRecord.id.in_(ids[i : i + chunk]))
                .values(trust_tier=target)
            )
    for i in range(0, len(events), chunk):
        db.execute(insert(TrustEvent), events[i : i + chunk])
    return {t: len(ids) for t, ids in by_target.items()}
