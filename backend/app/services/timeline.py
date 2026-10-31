"""Per-APAAR enrolment timelines and the deterministic timeline checks (spec 9.5, FR-13).

Pure functions over `EnrolmentView` snapshots (no DB access), used by the rule engine.

Intervals: an enrolment runs from `admission_date` to `end_date`; when the end date is not
known yet it is projected as admission + programme duration (one year less for lateral
entry). Two intervals overlap when each starts before the other ends.

Checks:
- TIMELINE_INVALID_DATE: admission in the future, or end date before admission.
- TIMELINE_TOO_SHORT: a GRADUATED enrolment ends earlier than admission + minimum duration
  (programme duration minus a configurable tolerance, rule
  `timeline.min_duration_tolerance_months`).
- TIMELINE_LATERAL_NO_PRIOR: lateral entry with no completed (GRADUATED) enrolment that
  ended on or before the admission date.
- TIMELINE_OVERLAP: two overlapping FULL_TIME enrolments; decided by the rule engine after
  the transfer-aware rule (`services/transfer.py`).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from app.db.models import EntryType, RecordStatus, StudyMode

ONGOING_STATUSES = frozenset(
    {RecordStatus.PENDING_APPROVAL, RecordStatus.ACTIVE, RecordStatus.NEEDS_CORRECTION}
)


@dataclass(frozen=True)
class EnrolmentView:
    """What the timeline logic needs to know about one enrolment (AICTE-internal)."""

    record_id: int | None
    student_id: int | None
    institution_id: int
    institution_programme_id: int | None
    institution_code: str
    programme_code: str
    level: str
    duration_years: int
    academic_year: str
    admission_date: date | None
    end_date: date | None
    status: str
    mode: str
    entry_type: str

    @property
    def full_time(self) -> bool:
        return self.mode == StudyMode.FULL_TIME

    @property
    def lateral(self) -> bool:
        return self.entry_type == EntryType.LATERAL

    def summary(self) -> dict:
        """AICTE-only evidence for cases."""
        start, end = interval(self)
        return {
            "record_id": self.record_id,
            "institution_id": self.institution_id,
            "institution_code": self.institution_code,
            "programme_code": self.programme_code,
            "academic_year": self.academic_year,
            "admission_date": _iso(self.admission_date),
            "end_date": _iso(self.end_date),
            "projected_end": _iso(end) if self.end_date is None else None,
            "status": str(self.status),
            "mode": str(self.mode),
            "entry_type": str(self.entry_type),
        }


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def nominal_years(e: EnrolmentView) -> int:
    years = max(1, int(e.duration_years or 1))
    return years - 1 if e.lateral and years > 1 else years


def projected_end(e: EnrolmentView) -> date | None:
    if e.end_date is not None:
        return e.end_date
    if e.admission_date is None:
        return None
    return add_months(e.admission_date, 12 * nominal_years(e))


def interval(e: EnrolmentView) -> tuple[date | None, date | None]:
    return e.admission_date, projected_end(e)


def overlaps(a: EnrolmentView, b: EnrolmentView) -> bool:
    a_start, a_end = interval(a)
    b_start, b_end = interval(b)
    if None in (a_start, a_end, b_start, b_end):
        return False
    return a_start < b_end and b_start < a_end


def minimum_completion(e: EnrolmentView, tolerance_months: int) -> date | None:
    if e.admission_date is None:
        return None
    return add_months(e.admission_date, max(0, 12 * nominal_years(e) - tolerance_months))


def is_too_short(e: EnrolmentView, tolerance_months: int) -> bool:
    if e.status != RecordStatus.GRADUATED or e.end_date is None:
        return False
    earliest = minimum_completion(e, tolerance_months)
    return earliest is not None and e.end_date < earliest


def invalid_date_kinds(e: EnrolmentView, today: date) -> list[str]:
    kinds = []
    if e.admission_date and e.admission_date > today:
        kinds.append("future")
    if e.admission_date and e.end_date and e.end_date < e.admission_date:
        kinds.append("end_before_admission")
    return kinds


def completed_priors(candidate: EnrolmentView, others: list[EnrolmentView]) -> list[EnrolmentView]:
    """Completed (GRADUATED) enrolments that ended on or before the candidate's admission."""
    if candidate.admission_date is None:
        return []
    return [
        o
        for o in others
        if o.status == RecordStatus.GRADUATED
        and o.end_date is not None
        and o.end_date <= candidate.admission_date
    ]


def build_timeline(enrolments: list[EnrolmentView]) -> list[EnrolmentView]:
    """The per-APAAR timeline: enrolments in admission order."""
    return sorted(enrolments, key=lambda e: (e.admission_date or date.max, e.record_id or 0))
