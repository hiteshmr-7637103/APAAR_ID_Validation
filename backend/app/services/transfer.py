"""Transfer-aware rule (spec 9.1, FR-14): applied BEFORE any overlap/duplicate decision.

If the other enrolment's `end_date <= new admission_date`, the two are sequential, not
duplicates. When the earlier enrolment was left before completion (WITHDRAWN, DROPPED, ...)
this is a transfer: INFO `TRANSFER_DETECTED`, no case. When it was completed (GRADUATED)
it is a prior qualification (used by the lateral-entry check), not a transfer.
"""

from __future__ import annotations

import enum

from app.db.models import RecordStatus
from app.services.timeline import EnrolmentView, overlaps


class Relation(enum.StrEnum):
    TRANSFER = "TRANSFER"  # other left before this admission: genuine move
    PRIOR_COMPLETED = "PRIOR_COMPLETED"  # other completed before this admission
    LATER = "LATER"  # this enrolment ended before the other started
    OVERLAP = "OVERLAP"  # intervals overlap (possible duplicate / impossible timeline)
    DISJOINT = "DISJOINT"  # no overlap, nothing to report


def is_transfer(new: EnrolmentView, other: EnrolmentView) -> bool:
    return (
        other.end_date is not None
        and new.admission_date is not None
        and other.end_date <= new.admission_date
        and other.status != RecordStatus.GRADUATED
    )


def relation(new: EnrolmentView, other: EnrolmentView) -> Relation:
    """How `other` relates to `new` (same APAAR ID). Transfer logic comes first."""
    if (
        other.end_date is not None
        and new.admission_date is not None
        and other.end_date <= new.admission_date
    ):
        if other.status == RecordStatus.GRADUATED:
            return Relation.PRIOR_COMPLETED
        return Relation.TRANSFER
    if (
        new.end_date is not None
        and other.admission_date is not None
        and new.end_date <= other.admission_date
    ):
        return Relation.LATER
    return Relation.OVERLAP if overlaps(new, other) else Relation.DISJOINT
