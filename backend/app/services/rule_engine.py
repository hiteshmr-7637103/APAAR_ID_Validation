"""Deterministic rule engine (spec 5.2, FR-6 to FR-13; timeline checks 9.5).

"Rules decide": every check here is arithmetic or lookup, never ML. The engine is pure with
respect to the database: it reads through a `RuleContext` and returns a
`ValidationReport`; callers decide what to store. The same engine serves the dry-run
endpoint (`POST /validate/record`), single-record creation and the replay of seeded data.

Outputs:
- `outcomes`: institution-facing results (code, severity, field, message_key, message_en,
  evidence, suggested_fix). They never carry another institution's data: cross-institution
  findings appear only as the neutral INFO codes ENROLMENT_ELSEWHERE_NEUTRAL /
  TIMELINE_CHECK_NEUTRAL, i.e. a boolean.
- `cases`: AICTE-only case specs (DUP_EXACT_CROSS, cross-institution TIMELINE) with the
  full evidence. Case keys use the HMAC APAAR token (FR-37), never the raw ID.

The Seat Ledger outcome SEAT_OVER_CAP (FR-10) is produced by `services/seat_ledger.py`
because it depends on the atomic seat reservation, not on the record alone.

Pairwise checks compare a record only with enrolments submitted *before* it (all of them
for a new record; lower record ids when re-validating stored data), so a duplicate pair is
reported once, on the later submission, exactly as it would be at submission time.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    AdmissionCategory,
    CaseType,
    CaseVisibility,
    EntryType,
    Gender,
    Institution,
    InstitutionProgramme,
    LedgerKey,
    Programme,
    RecordStatus,
    Severity,
    Shift,
    Student,
    StudentRecord,
    StudyMode,
)
from app.services.apaar_client import ApaarRecord, get_apaar_client
from app.services.messages import render
from app.services.names import name_similarity
from app.services.privacy_tokens import apaar_token
from app.services.quota import active_rules, default_rule_values, ledger_key_for
from app.services.timeline import (
    EnrolmentView,
    completed_priors,
    invalid_date_kinds,
    is_too_short,
    minimum_completion,
    nominal_years,
)
from app.services.transfer import Relation, relation

APAAR_RE = re.compile(r"^\d{12}$")
INDIAN_NATIONALITY = frozenset({"", "indian", "india", "in", "ind"})

# Every code the engine (and the Seat Ledger) can emit, with its fixed severity.
RULE_CODES: dict[str, Severity] = {
    "APAAR_INVALID": Severity.ERROR,
    "APAAR_SUSPICIOUS": Severity.WARNING,
    "APAAR_NOT_FOUND": Severity.ERROR,
    "APAAR_MISMATCH": Severity.WARNING,
    "DUP_EXACT_SAME_INST": Severity.ERROR,
    "ENROLMENT_ELSEWHERE_NEUTRAL": Severity.INFO,
    "TRANSFER_DETECTED": Severity.INFO,
    "SEAT_OVER_CAP": Severity.WARNING,
    "FN_NATIONALITY_INVALID": Severity.ERROR,
    "FN_ID_DOCUMENT_MISSING": Severity.WARNING,
    "OCI_NUMBER_MISSING": Severity.ERROR,
    "NRI_SPONSOR_MISSING": Severity.ERROR,
    "ADMISSION_OUTSIDE_WINDOW": Severity.WARNING,
    "DOB_IMPLAUSIBLE": Severity.WARNING,
    "REQUIRED_FIELD_MISSING": Severity.ERROR,
    "FIELD_INVALID": Severity.ERROR,
    "PROGRAMME_NOT_APPROVED": Severity.ERROR,
    "TIMELINE_OVERLAP": Severity.WARNING,
    "TIMELINE_TOO_SHORT": Severity.WARNING,
    "TIMELINE_LATERAL_NO_PRIOR": Severity.WARNING,
    "TIMELINE_INVALID_DATE": Severity.ERROR,
    "TIMELINE_CHECK_NEUTRAL": Severity.INFO,
}

FIELD_LABELS = {
    "apaar_id": "APAAR ID",
    "full_name": "Full name",
    "dob": "Date of birth",
    "gender": "Gender",
    "programme_code": "Programme",
    "shift": "Shift",
    "admission_date": "Admission date",
    "admission_category": "Admission category",
    "entry_type": "Entry type",
    "mode": "Mode",
}
_ENUM_FIELDS: dict[str, type] = {
    "gender": Gender,
    "shift": Shift,
    "admission_category": AdmissionCategory,
    "entry_type": EntryType,
    "mode": StudyMode,
}


# --------------------------------------------------------------------------- data types


@dataclass
class RecordInput:
    """A candidate record as submitted (spec 7.2 columns) plus replay metadata."""

    institution_id: int
    apaar_id: str | None = None
    full_name: str | None = None
    dob: date | None = None
    gender: str | None = None
    programme_code: str | None = None
    shift: str | None = Shift.REGULAR.value
    academic_year: str | None = None
    admission_date: date | None = None
    end_date: date | None = None
    admission_category: str | None = None
    entry_type: str | None = EntryType.REGULAR.value
    mode: str | None = StudyMode.FULL_TIME.value
    nationality: str | None = None
    is_nri: bool = False
    nri_sponsor: str | None = None
    is_oci: bool = False
    oci_number: str | None = None
    is_foreign_national: bool = False
    id_document_note: str | None = None
    student_college_id: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    # Set when re-validating a stored record (replay): excludes itself, orders pairs.
    record_id: int | None = None
    status: str = RecordStatus.PENDING_APPROVAL.value
    institution_programme_id: int | None = None


@dataclass(frozen=True)
class IPView:
    id: int
    institution_id: int
    institution_code: str
    programme_code: str
    programme_name: str
    level: str
    duration_years: int
    academic_year: str
    shift: str
    approved_intake: int


@dataclass
class Outcome:
    code: str
    severity: Severity
    field: str | None
    message_key: str
    message_en: str
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_fix: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": str(self.severity),
            "field": self.field,
            "message_key": self.message_key,
            "message_en": self.message_en,
            "evidence": self.evidence,
            "suggested_fix": self.suggested_fix,
        }


@dataclass
class CaseSpec:
    """An AICTE-only case the caller should open (or merge into an open one by `key`).
    `None` in `enrolments` stands for the candidate record before it has an id."""

    key: str
    type: CaseType
    code: str
    priority: int
    enrolments: list[dict[str, Any]]
    explanation_en: str
    visibility: CaseVisibility = CaseVisibility.AICTE_ONLY
    severity: Severity = Severity.WARNING
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    outcomes: list[Outcome]
    cases: list[CaseSpec]
    ip: IPView | None
    ledger_key: LedgerKey | None
    apaar_status: dict[str, Any]

    @property
    def has_error(self) -> bool:
        return any(o.severity == Severity.ERROR for o in self.outcomes)

    @property
    def codes(self) -> list[str]:
        return [o.code for o in self.outcomes]

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for o in self.outcomes:
            out[str(o.severity)] += 1
        return out


def outcome(
    code: str,
    field_name: str | None,
    variant: str | None = None,
    *,
    evidence=None,
    suggested_fix=None,
    **params: Any,
) -> Outcome:
    key = f"validation.{code.lower()}" + (f".{variant}" if variant else "")
    return Outcome(
        code=code,
        severity=RULE_CODES[code],
        field=field_name,
        message_key=key,
        message_en=render(key, **params),
        evidence=evidence or {},
        suggested_fix=suggested_fix,
    )


# --------------------------------------------------------------------------- context


class RuleContext(ABC):
    """Read-only view of the data the rules need."""

    today: date
    rules: dict[str, Any]

    @abstractmethod
    def approved_ips(self, institution_id: int, academic_year: str) -> list[IPView]: ...

    @abstractmethod
    def ip_by_id(self, ip_id: int) -> IPView | None: ...

    @abstractmethod
    def programme(self, code: str) -> tuple[str, int] | None:
        """(level, duration_years) from the canonical catalogue."""

    @abstractmethod
    def enrolments(self, apaar_id: str) -> list[EnrolmentView]: ...

    @abstractmethod
    def registry(self, apaar_id: str) -> ApaarRecord | None: ...

    def ip_for(self, institution_id: int, programme_code: str, year: str, shift: str):
        for ip in self.approved_ips(institution_id, year):
            if ip.programme_code == programme_code and ip.shift == shift:
                return ip
        return None


class MemoryRuleContext(RuleContext):
    """In-memory context (unit tests, scripted checks)."""

    def __init__(
        self,
        ips: list[IPView],
        enrolments: dict[str, list[EnrolmentView]] | None = None,
        registry: dict[str, ApaarRecord] | None = None,
        today: date | None = None,
        rules: dict[str, Any] | None = None,
        programmes: dict[str, tuple[str, int]] | None = None,
    ) -> None:
        self.ips = ips
        self._enrolments = enrolments or {}
        self._registry = registry or {}
        self.today = today or date.today()
        self.rules = rules or default_rule_values()
        self._programmes = programmes or {
            ip.programme_code: (ip.level, ip.duration_years) for ip in ips
        }

    def approved_ips(self, institution_id, academic_year):
        return [
            ip
            for ip in self.ips
            if ip.institution_id == institution_id and ip.academic_year == academic_year
        ]

    def ip_by_id(self, ip_id):
        return next((ip for ip in self.ips if ip.id == ip_id), None)

    def programme(self, code):
        return self._programmes.get(code)

    def enrolments(self, apaar_id):
        return list(self._enrolments.get(apaar_id, []))

    def registry(self, apaar_id):
        return self._registry.get(apaar_id)


def _ip_query():
    return (
        select(
            InstitutionProgramme.id,
            InstitutionProgramme.institution_id,
            Institution.code,
            Programme.canonical_code,
            Programme.name,
            Programme.level,
            Programme.duration_years,
            InstitutionProgramme.academic_year,
            InstitutionProgramme.shift,
            InstitutionProgramme.approved_intake,
        )
        .join(Institution, Institution.id == InstitutionProgramme.institution_id)
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
    )


def _ip_view(row) -> IPView:
    return IPView(
        id=row[0],
        institution_id=row[1],
        institution_code=row[2],
        programme_code=row[3],
        programme_name=row[4],
        level=str(row[5]),
        duration_years=row[6],
        academic_year=row[7],
        shift=str(row[8]),
        approved_intake=row[9],
    )


def enrolment_query():
    return (
        select(
            StudentRecord.id,
            StudentRecord.student_id,
            StudentRecord.institution_id,
            StudentRecord.institution_programme_id,
            Institution.code,
            Programme.canonical_code,
            Programme.level,
            Programme.duration_years,
            InstitutionProgramme.academic_year,
            StudentRecord.admission_date,
            StudentRecord.end_date,
            StudentRecord.status,
            StudentRecord.mode,
            StudentRecord.entry_type,
            Student.apaar_id,
        )
        .join(Student, Student.id == StudentRecord.student_id)
        .join(
            InstitutionProgramme,
            InstitutionProgramme.id == StudentRecord.institution_programme_id,
        )
        .join(Programme, Programme.id == InstitutionProgramme.programme_id)
        .join(Institution, Institution.id == StudentRecord.institution_id)
    )


def enrolment_view(row) -> EnrolmentView:
    return EnrolmentView(
        record_id=row[0],
        student_id=row[1],
        institution_id=row[2],
        institution_programme_id=row[3],
        institution_code=row[4],
        programme_code=row[5],
        level=str(row[6]),
        duration_years=row[7],
        academic_year=row[8],
        admission_date=row[9],
        end_date=row[10],
        status=str(row[11]),
        mode=str(row[12]),
        entry_type=str(row[13]),
    )


class DbRuleContext(RuleContext):
    """Database-backed context with caches; `prefetch_all()` loads everything at once for
    bulk validation (replay, large dry runs)."""

    def __init__(self, db: Session, today: date | None = None, rules=None) -> None:
        self.db = db
        self.today = today or date.today()
        self.rules = rules if rules is not None else active_rules(db)
        self._client = get_apaar_client(db)
        self._ips_by_inst: dict[int, list[IPView]] = {}
        self._ip_by_id: dict[int, IPView] = {}
        self._enrol: dict[str, list[EnrolmentView]] = {}
        self._registry: dict[str, ApaarRecord | None] = {}
        self._programmes: dict[str, tuple[str, int]] | None = None
        self._all_loaded = False

    def _add_ips(self, rows) -> None:
        for row in rows:
            ip = _ip_view(row)
            if ip.id not in self._ip_by_id:
                self._ip_by_id[ip.id] = ip
                self._ips_by_inst.setdefault(ip.institution_id, []).append(ip)

    def _load_inst(self, institution_id: int) -> None:
        if institution_id not in self._ips_by_inst:
            self._ips_by_inst[institution_id] = []
            self._add_ips(
                self.db.execute(
                    _ip_query().where(InstitutionProgramme.institution_id == institution_id)
                ).all()
            )

    def approved_ips(self, institution_id, academic_year):
        if not self._all_loaded:
            self._load_inst(institution_id)
        return [
            ip
            for ip in self._ips_by_inst.get(institution_id, [])
            if ip.academic_year == academic_year
        ]

    def ip_by_id(self, ip_id):
        if ip_id not in self._ip_by_id and not self._all_loaded:
            inst = self.db.scalar(
                select(InstitutionProgramme.institution_id).where(InstitutionProgramme.id == ip_id)
            )
            if inst is not None:
                self._load_inst(inst)
        return self._ip_by_id.get(ip_id)

    def programme(self, code):
        if self._programmes is None:
            self._programmes = {
                c: (str(level), years)
                for c, level, years in self.db.execute(
                    select(Programme.canonical_code, Programme.level, Programme.duration_years)
                ).all()
            }
        return self._programmes.get(code)

    def enrolments(self, apaar_id):
        if apaar_id not in self._enrol:
            if self._all_loaded:
                return []
            rows = self.db.execute(enrolment_query().where(Student.apaar_id == apaar_id)).all()
            self._enrol[apaar_id] = [enrolment_view(r) for r in rows]
        return list(self._enrol[apaar_id])

    def registry(self, apaar_id):
        if apaar_id not in self._registry:
            if self._all_loaded:
                return None
            self._registry[apaar_id] = self._client.lookup(apaar_id)
        return self._registry[apaar_id]

    def prefetch_all(self) -> None:
        self._add_ips(self.db.execute(_ip_query()).all())
        for row in self.db.execute(enrolment_query().where(Student.apaar_id.is_not(None))):
            self._enrol.setdefault(row[14], []).append(enrolment_view(row))
        found = self._client.lookup_many(self._enrol.keys())
        self._registry = {a: found.get(a) for a in self._enrol}
        self._all_loaded = True

    def prefetch_registry(self, apaar_ids) -> None:
        """Bulk registry lookup for IDs that may have no enrolment yet (a new upload);
        needed after `prefetch_all()`, which only loads IDs that are already enrolled."""
        wanted = [a for a in set(apaar_ids) if a and a not in self._registry]
        if wanted:
            found = self._client.lookup_many(wanted)
            self._registry.update({a: found.get(a) for a in wanted})


# --------------------------------------------------------------------------- helpers


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def suspicious_pattern(apaar_id: str, min_run: int = 6) -> str | None:
    """'all digits the same' / 'sequential digits', else None (FR-6)."""
    if len(set(apaar_id)) == 1:
        return "all digits the same"
    digits = [int(c) for c in apaar_id]
    for step in (1, -1):
        run = 1
        for a, b in zip(digits, digits[1:], strict=False):
            run = run + 1 if b - a == step else 1
            if run >= min_run:
                return "sequential digits"
    return None


def suggest_programmes(query: str | None, ips: list[IPView], limit: int = 3) -> list[dict]:
    """Programme suggestion placeholder (ML-4 TF-IDF mapping arrives in Phase 5): exact
    code, then code/name prefix, then substring, against the approved list."""
    if not query:
        return []
    q = query.strip().lower()
    scored = []
    for ip in ips:
        code, name = ip.programme_code.lower(), ip.programme_name.lower()
        if q == code:
            rank = 0
        elif code.startswith(q) or name.startswith(q):
            rank = 1
        elif q in code or q in name:
            rank = 2
        else:
            continue
        scored.append((rank, ip.programme_code, ip.shift, ip))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [
        {"programme_code": ip.programme_code, "shift": ip.shift, "name": ip.programme_name}
        for _, _, _, ip in scored[:limit]
    ]


def admission_window(rules: dict[str, Any], year: str) -> tuple[date, date]:
    w = rules.get("academic_year.admission_window", {}).get(year)
    if w:
        return date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
    start = int(year[:4])
    return date(start, 7, 1), date(start, 9, 30)


def _age(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


# --------------------------------------------------------------------------- the engine


def validate_record(inp: RecordInput, ctx: RuleContext) -> ValidationReport:
    out: list[Outcome] = []
    cases: list[CaseSpec] = []

    valid_enums = _required_and_enums(inp, out)
    year = inp.academic_year or settings.current_academic_year
    shift = _clean(inp.shift) or Shift.REGULAR.value
    programme_code = _clean(inp.programme_code)

    ip: IPView | None = None
    if inp.institution_programme_id is not None:
        ip = ctx.ip_by_id(inp.institution_programme_id)
    elif programme_code:
        ip = ctx.ip_for(inp.institution_id, programme_code, year, shift)
        if ip is None:
            approved = ctx.approved_ips(inp.institution_id, year)
            out.append(
                outcome(
                    "PROGRAMME_NOT_APPROVED",
                    "programme_code",
                    programme=programme_code,
                    shift=shift,
                    year=year,
                    evidence={"programme_code": programme_code, "shift": shift, "year": year},
                    suggested_fix={"programmes": suggest_programmes(programme_code, approved)},
                )
            )
    if ip is not None:
        year, shift, programme_code = ip.academic_year, ip.shift, ip.programme_code

    ledger_key = None
    if "admission_category" in valid_enums and "entry_type" in valid_enums:
        ledger_key = ledger_key_for(inp.admission_category, inp.entry_type)

    apaar = _clean(inp.apaar_id)
    apaar_status = _apaar_checks(inp, apaar, ctx, out)
    _consistency_checks(inp, ip, year, ctx, out)

    if ip is not None:
        level, duration = ip.level, ip.duration_years
    else:
        level, duration = (ctx.programme(programme_code) if programme_code else None) or (
            "OTHER",
            1,
        )
    candidate = EnrolmentView(
        record_id=inp.record_id,
        student_id=None,
        institution_id=inp.institution_id,
        institution_programme_id=ip.id if ip else None,
        institution_code=ip.institution_code if ip else "",
        programme_code=programme_code or "",
        level=level,
        duration_years=duration,
        academic_year=year,
        admission_date=inp.admission_date,
        end_date=inp.end_date,
        status=str(inp.status),
        mode=_clean(inp.mode) or StudyMode.FULL_TIME.value,
        entry_type=_clean(inp.entry_type) or EntryType.REGULAR.value,
    )
    _own_timeline_checks(candidate, ctx, out)
    if apaar and APAAR_RE.match(apaar):
        _pairwise_checks(candidate, apaar, ctx, out, cases)

    return ValidationReport(out, cases, ip, ledger_key, apaar_status)


def _required_and_enums(inp: RecordInput, out: list[Outcome]) -> set[str]:
    valid: set[str] = set()
    for name in ("full_name", "dob", "admission_date", "programme_code"):
        value = getattr(inp, name)
        if value is None or (isinstance(value, str) and not value.strip()):
            out.append(
                outcome(
                    "REQUIRED_FIELD_MISSING",
                    name,
                    label=FIELD_LABELS[name],
                    evidence={"field": name},
                )
            )
    for name, enum_cls in _ENUM_FIELDS.items():
        value = _clean(getattr(inp, name))
        if value is None:
            out.append(
                outcome(
                    "REQUIRED_FIELD_MISSING",
                    name,
                    label=FIELD_LABELS[name],
                    evidence={"field": name},
                )
            )
            continue
        allowed = [e.value for e in enum_cls]
        if value not in allowed:
            out.append(
                outcome(
                    "FIELD_INVALID",
                    name,
                    label=FIELD_LABELS[name],
                    value=value,
                    allowed=", ".join(allowed),
                    evidence={"field": name, "value": value},
                    suggested_fix={"allowed": allowed},
                )
            )
            continue
        valid.add(name)
    return valid


def _apaar_checks(inp: RecordInput, apaar: str | None, ctx: RuleContext, out) -> dict:
    """FR-6 (format, suspicious patterns) and FR-7 (MOCK registry existence and match).
    The status returned to institutions holds booleans only, never registry data."""
    status: dict[str, Any] = {
        "source": "MOCK",
        "format_ok": False,
        "suspicious": False,
        "found": None,
        "name_match": None,
        "dob_match": None,
    }
    if not apaar:
        out.append(outcome("APAAR_INVALID", "apaar_id", "missing", evidence={"reason": "missing"}))
        return status
    if not APAAR_RE.match(apaar):
        out.append(
            outcome(
                "APAAR_INVALID",
                "apaar_id",
                "format",
                value=apaar,
                evidence={"reason": "format", "length": len(apaar)},
                suggested_fix={"hint": "Enter the 12 digits printed on the APAAR card."},
            )
        )
        return status
    status["format_ok"] = True
    min_run = int(ctx.rules.get("validation.apaar_suspicious", {}).get("min_sequential_run", 6))
    pattern = suspicious_pattern(apaar, min_run)
    if pattern:
        status["suspicious"] = True
        out.append(
            outcome(
                "APAAR_SUSPICIOUS",
                "apaar_id",
                pattern=pattern,
                evidence={"pattern": pattern},
            )
        )
    reg = ctx.registry(apaar)
    if reg is None:
        status["found"] = False
        out.append(outcome("APAAR_NOT_FOUND", "apaar_id", evidence={"source": "MOCK"}))
        return status
    status["found"] = True
    min_score = float(ctx.rules.get("validation.name_match", {}).get("min_score", 85))
    mismatched = []
    evidence: dict[str, Any] = {"source": "MOCK"}
    if _clean(inp.full_name):
        score = name_similarity(inp.full_name, reg.full_name)
        status["name_match"] = score >= min_score
        evidence["name_score"] = round(score, 1)
        if not status["name_match"]:
            mismatched.append("name")
    if inp.dob is not None:
        status["dob_match"] = inp.dob == reg.dob
        if not status["dob_match"]:
            mismatched.append("date of birth")
    if mismatched:
        evidence.update(name_match=status["name_match"], dob_match=status["dob_match"])
        field_name = "full_name" if mismatched[0] == "name" else "dob"
        out.append(
            outcome(
                "APAAR_MISMATCH",
                field_name,
                what=" and ".join(mismatched),
                evidence=evidence,
            )
        )
    return status


def _consistency_checks(inp: RecordInput, ip: IPView | None, year: str, ctx, out) -> None:
    """FR-12."""
    category = _clean(inp.admission_category)
    if category == AdmissionCategory.FN or inp.is_foreign_national:
        nationality = (_clean(inp.nationality) or "").lower()
        if nationality in INDIAN_NATIONALITY:
            out.append(
                outcome(
                    "FN_NATIONALITY_INVALID",
                    "nationality",
                    evidence={"nationality": _clean(inp.nationality)},
                    suggested_fix={"hint": "Enter the student's country of citizenship."},
                )
            )
        if not _clean(inp.id_document_note):
            out.append(outcome("FN_ID_DOCUMENT_MISSING", "id_document_note"))
    if category == AdmissionCategory.OCI or inp.is_oci:
        if not _clean(inp.oci_number):
            out.append(outcome("OCI_NUMBER_MISSING", "oci_number"))
    if category == AdmissionCategory.NRI or inp.is_nri:
        if not _clean(inp.nri_sponsor):
            out.append(outcome("NRI_SPONSOR_MISSING", "nri_sponsor"))

    adm = inp.admission_date
    if adm is not None:
        start, end = admission_window(ctx.rules, year)
        if not (start <= adm <= end):
            out.append(
                outcome(
                    "ADMISSION_OUTSIDE_WINDOW",
                    "admission_date",
                    date=adm.isoformat(),
                    year=year,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    evidence={
                        "admission_date": adm.isoformat(),
                        "window_start": start.isoformat(),
                        "window_end": end.isoformat(),
                        "academic_year": year,
                    },
                )
            )
    if adm is not None and inp.dob is not None:
        level = ip.level if ip else "OTHER"
        ranges = ctx.rules.get("validation.age_at_admission", {})
        low, high = ranges.get(level) or ranges.get("OTHER") or [14, 60]
        age = _age(inp.dob, adm)
        if inp.dob >= adm or not (low <= age <= high):
            out.append(
                outcome(
                    "DOB_IMPLAUSIBLE",
                    "dob",
                    age=age,
                    low=low,
                    high=high,
                    level=level,
                    evidence={"age_at_admission": age, "range": [low, high], "level": level},
                )
            )


def _own_timeline_checks(e: EnrolmentView, ctx: RuleContext, out) -> None:
    for kind in invalid_date_kinds(e, ctx.today):
        if kind == "future":
            out.append(
                outcome(
                    "TIMELINE_INVALID_DATE",
                    "admission_date",
                    "future",
                    date=e.admission_date.isoformat(),
                    evidence={"kind": kind, "today": ctx.today.isoformat()},
                )
            )
        else:
            out.append(
                outcome(
                    "TIMELINE_INVALID_DATE",
                    "end_date",
                    "end_before_admission",
                    end=e.end_date.isoformat(),
                    admission=e.admission_date.isoformat(),
                    evidence={"kind": kind},
                )
            )
    tol = _tolerance(ctx)
    if is_too_short(e, tol):
        out.append(
            outcome(
                "TIMELINE_TOO_SHORT",
                "end_date",
                "own",
                end=e.end_date.isoformat(),
                admission=e.admission_date.isoformat(),
                years=nominal_years(e),
                programme=e.programme_code,
                evidence={
                    "minimum_completion": minimum_completion(e, tol).isoformat(),
                    "tolerance_months": tol,
                },
            )
        )


def _tolerance(ctx: RuleContext) -> int:
    return int(ctx.rules.get("timeline.min_duration_tolerance_months", {}).get("months", 6))


def _pairwise_checks(c: EnrolmentView, apaar: str, ctx: RuleContext, out, cases) -> None:
    """FR-8, FR-9, FR-13 overlap/lateral checks with the transfer-aware rule first."""
    others = [
        o
        for o in ctx.enrolments(apaar)
        if o.record_id != c.record_id and o.status != RecordStatus.REJECTED
    ]
    earlier = [o for o in others if c.record_id is None or (o.record_id or 0) < c.record_id]
    token = apaar_token(apaar)
    me = c.summary()
    dup_cross: list[EnrolmentView] = []
    overlap_cross: list[EnrolmentView] = []
    transfer = False

    for o in earlier:
        rel = relation(c, o)
        if rel == Relation.TRANSFER:
            transfer = True
            continue
        if rel != Relation.OVERLAP:
            continue
        if o.institution_id == c.institution_id:
            if o.programme_code == c.programme_code and o.academic_year == c.academic_year:
                out.append(
                    outcome(
                        "DUP_EXACT_SAME_INST",
                        "apaar_id",
                        programme=c.programme_code,
                        year=c.academic_year,
                        other_record_id=o.record_id,
                        evidence={"other_record_id": o.record_id},
                        suggested_fix={"hint": "Update the existing record instead."},
                    )
                )
            elif c.full_time and o.full_time:
                out.append(
                    outcome(
                        "TIMELINE_OVERLAP",
                        "admission_date",
                        other_record_id=o.record_id,
                        other_programme=o.programme_code,
                        evidence={"other_record_id": o.record_id},
                    )
                )
        elif o.academic_year == c.academic_year:
            dup_cross.append(o)
        elif c.full_time and o.full_time:
            overlap_cross.append(o)

    neutral_elsewhere = bool(dup_cross or overlap_cross)
    neutral_timeline = False
    if dup_cross:
        cases.append(
            CaseSpec(
                key=f"DUP_EXACT_CROSS:{token}",
                type=CaseType.DUP_EXACT_CROSS,
                code="DUP_EXACT_CROSS",
                priority=1,
                enrolments=[me, *[o.summary() for o in dup_cross]],
                explanation_en=(
                    "The same APAAR ID is enrolled at more than one institution with "
                    f"overlapping enrolment intervals in {c.academic_year} (FR-9)."
                ),
                extra={"apaar_token": token, "rule": "FR-9"},
            )
        )
    if overlap_cross:
        cases.append(
            CaseSpec(
                key=f"TIMELINE:TIMELINE_OVERLAP:{token}",
                type=CaseType.TIMELINE,
                code="TIMELINE_OVERLAP",
                priority=2,
                enrolments=[me, *[o.summary() for o in overlap_cross]],
                explanation_en=(
                    "Two full-time enrolments of the same APAAR ID at different institutions "
                    "overlap in time (spec 9.5)."
                ),
                extra={"apaar_token": token, "rule": "FR-13"},
            )
        )

    if c.lateral and c.admission_date is not None:
        tol = _tolerance(ctx)
        priors = completed_priors(c, others)
        if not priors:
            out.append(
                outcome(
                    "TIMELINE_LATERAL_NO_PRIOR",
                    "entry_type",
                    evidence={"completed_prior_records": 0},
                    suggested_fix={"hint": "Check the entry type, or attach the diploma."},
                )
            )
        elif all(is_too_short(p, tol) for p in priors):
            short_cross = []
            for p in priors:
                if p.institution_id == c.institution_id:
                    out.append(
                        outcome(
                            "TIMELINE_TOO_SHORT",
                            "entry_type",
                            "prior",
                            other_record_id=p.record_id,
                            evidence={"other_record_id": p.record_id},
                        )
                    )
                else:
                    short_cross.append(p)
            if short_cross:
                neutral_timeline = True
                cases.append(
                    CaseSpec(
                        key=f"TIMELINE:TIMELINE_TOO_SHORT:{token}",
                        type=CaseType.TIMELINE,
                        code="TIMELINE_TOO_SHORT",
                        priority=2,
                        enrolments=[me, *[p.summary() for p in short_cross]],
                        explanation_en=(
                            "Lateral entry rests on a prior qualification that was completed "
                            "sooner than its minimum duration allows (spec 9.5)."
                        ),
                        extra={"apaar_token": token, "rule": "FR-13", "tolerance_months": tol},
                    )
                )

    if neutral_elsewhere:
        out.append(outcome("ENROLMENT_ELSEWHERE_NEUTRAL", "apaar_id"))
    if neutral_timeline:
        out.append(outcome("TIMELINE_CHECK_NEUTRAL", "entry_type"))
    if transfer:
        out.append(outcome("TRANSFER_DETECTED", "admission_date"))
