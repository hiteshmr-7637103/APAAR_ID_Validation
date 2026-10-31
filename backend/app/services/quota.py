"""Seat buckets and default quota rules (spec 7.3), as data.

Defaults are seeded into the versioned `quota_rules` table and must be **VERIFIED** against
the current AICTE Approval Process Handbook; they are not legal facts. The Seat Ledger and
the rule engine read the *active* version of each rule through `active_rules()` and caps
through `compute_cap`, so changing a rule needs no code change (rules-as-data, P21).

Active version of a key = the highest `version` whose `effective_from` is not in the
future. Keys with no row fall back to the defaults below (e.g. a fresh test database).

Simplification (documented): the one-third CIWGC reservation inside the FN/OCI/PIO/CIWGC
pool, and reversion between those pools, are not modelled; the pool has one cap.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AdmissionCategory, EntryType, LedgerKey, QuotaRule, Shift

SUPERNUMERARY_FN_CATEGORIES = frozenset(
    {
        AdmissionCategory.FN,
        AdmissionCategory.OCI,
        AdmissionCategory.PIO,
        AdmissionCategory.CIWGC,
    }
)

# key -> (value_json, description). All percentages are VERIFY defaults.
DEFAULT_QUOTA_RULES: dict[str, tuple[dict[str, Any], str]] = {
    "bucket.APPROVED": (
        {"source": "approved_intake"},
        "APPROVED bucket cap = approved intake (excludes supernumerary seats by definition).",
    ),
    "bucket.SUPERNUMERARY_FN_OCI_PIO_CIWGC": (
        {"pct": 0.15, "rounding": "floor", "regular_shift_only": True, "verify": True},
        "FN/OCI/PIO/CIWGC supernumerary seats as a share of approved intake, REGULAR shift "
        "only. Default 15%, VERIFY. CIWGC one-third reservation not modelled.",
    ),
    "bucket.SUPERNUMERARY_TFW": (
        {"pct": 0.05, "rounding": "floor", "verify": True},
        "Tuition Fee Waiver supernumerary seats as a share of approved intake. "
        "Default 5%, VERIFY.",
    ),
    "bucket.LATERAL": (
        {"pct": 0.20, "rounding": "ceil", "verify": True},
        "Lateral-entry seats as a share of approved intake. Default 20%, VERIFY.",
    ),
    "bucket.NRI": (
        {"default": 0, "by_institution_programme": {}},
        "NRI seats per institution-programme (key 'INST|PROG|YEAR|SHIFT'). Default 0, so "
        "an unapproved NRI admission is flagged.",
    ),
    "academic_year.admission_window": (
        {
            "2022-23": {"start": "2022-07-01", "end": "2022-09-30"},
            "2023-24": {"start": "2023-07-01", "end": "2023-09-30"},
            "2024-25": {"start": "2024-07-01", "end": "2024-09-30"},
            "2025-26": {"start": "2025-07-01", "end": "2025-09-30"},
        },
        "Admission window per academic year (CONFIGURABLE, VERIFY against the AICTE "
        "academic calendar).",
    ),
    # Phase 3 rule-engine settings (CONFIGURABLE; not legal facts).
    "timeline.min_duration_tolerance_months": (
        {"months": 6},
        "TIMELINE_TOO_SHORT fires when graduation is earlier than admission + programme "
        "duration minus this tolerance (academic years end in May-June). CONFIGURABLE.",
    ),
    "validation.age_at_admission": (
        {
            "UG": [16, 35],
            "DIPLOMA": [14, 35],
            "PG": [19, 60],
            "PGDM": [19, 60],
            "MCA": [19, 60],
            "OTHER": [14, 60],
        },
        "Plausible age range (years at admission) per programme level for DOB_IMPLAUSIBLE. "
        "CONFIGURABLE.",
    ),
    "validation.apaar_suspicious": (
        {"min_sequential_run": 6},
        "APAAR_SUSPICIOUS fires for all-same-digit IDs or an ascending/descending digit run "
        "of at least this length. CONFIGURABLE.",
    ),
    "validation.name_match": (
        {"min_score": 85},
        "Minimum RapidFuzz token-sort score (0-100) between normalised submitted and registry "
        "names before APAAR_MISMATCH fires (FR-7). CONFIGURABLE.",
    ),
}

# Keys whose change alters seat caps (the Seat Ledger recomputes caps when one changes).
CAP_RULE_KEYS = frozenset(k for k in DEFAULT_QUOTA_RULES if k.startswith("bucket."))


def active_rules(db: Session) -> dict[str, Any]:
    """{key: value_json} of the active version of every rule, defaults for missing keys."""
    rows = db.execute(
        select(QuotaRule.key, QuotaRule.value_json)
        .where(QuotaRule.effective_from <= func.now())
        .order_by(QuotaRule.key, QuotaRule.version.desc())
    ).all()
    values = default_rule_values()
    seen: set[str] = set()
    for key, value in rows:
        if key not in seen:
            seen.add(key)
            values[key] = value
    return values


def default_rule_values() -> dict[str, Any]:
    """{key: value_json} for the defaults (deep-copied enough for safe mutation)."""
    import copy

    return {k: copy.deepcopy(v) for k, (v, _) in DEFAULT_QUOTA_RULES.items()}


def ip_natural_key(inst_code: str, prog_code: str, academic_year: str, shift: str) -> str:
    return f"{inst_code}|{prog_code}|{academic_year}|{shift}"


def ledger_key_for(category: str | AdmissionCategory, entry_type: str | EntryType) -> LedgerKey:
    """Bucket mapping (spec 7.3), first match wins."""
    if EntryType(entry_type) == EntryType.LATERAL:
        return LedgerKey.LATERAL
    cat = AdmissionCategory(category)
    if cat in SUPERNUMERARY_FN_CATEGORIES:
        return LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC
    if cat == AdmissionCategory.TFW:
        return LedgerKey.SUPERNUMERARY_TFW
    if cat == AdmissionCategory.NRI:
        return LedgerKey.NRI
    return LedgerKey.APPROVED


def _pct_cap(intake: int, rule: dict[str, Any]) -> int:
    raw = intake * float(rule["pct"])
    return math.ceil(raw - 1e-9) if rule.get("rounding") == "ceil" else math.floor(raw + 1e-9)


def compute_cap(
    key: LedgerKey,
    approved_intake: int,
    shift: str | Shift,
    natural_key: str,
    rules: dict[str, Any],
) -> int:
    """Cap of one bucket for one institution-programme, from rule values."""
    if key == LedgerKey.APPROVED:
        return approved_intake
    if key == LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC:
        rule = rules["bucket.SUPERNUMERARY_FN_OCI_PIO_CIWGC"]
        if rule.get("regular_shift_only") and Shift(shift) != Shift.REGULAR:
            return 0
        return _pct_cap(approved_intake, rule)
    if key == LedgerKey.SUPERNUMERARY_TFW:
        return _pct_cap(approved_intake, rules["bucket.SUPERNUMERARY_TFW"])
    if key == LedgerKey.LATERAL:
        return _pct_cap(approved_intake, rules["bucket.LATERAL"])
    if key == LedgerKey.NRI:
        rule = rules["bucket.NRI"]
        return int(rule.get("by_institution_programme", {}).get(natural_key, rule["default"]))
    raise ValueError(f"unknown ledger key {key}")


def _is_pct(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and 0 <= v <= 1


def _is_count(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def validate_rule_value(key: str, value: Any) -> list[str]:
    """Shape checks for `PUT /rules/{key}`; returns a list of problems (empty = valid)."""
    if key not in DEFAULT_QUOTA_RULES:
        return [f"unknown rule key {key!r}"]
    if not isinstance(value, dict):
        return ["value must be a JSON object"]
    errs: list[str] = []
    if key == "bucket.APPROVED":
        if value.get("source") != "approved_intake":
            errs.append("APPROVED cap is the approved intake: source must be 'approved_intake'")
    elif key in (
        "bucket.SUPERNUMERARY_FN_OCI_PIO_CIWGC",
        "bucket.SUPERNUMERARY_TFW",
        "bucket.LATERAL",
    ):
        if not _is_pct(value.get("pct")):
            errs.append("pct must be a number between 0 and 1")
        if value.get("rounding", "floor") not in ("floor", "ceil"):
            errs.append("rounding must be 'floor' or 'ceil'")
        if "regular_shift_only" in value and not isinstance(value["regular_shift_only"], bool):
            errs.append("regular_shift_only must be true or false")
    elif key == "bucket.NRI":
        if not _is_count(value.get("default")):
            errs.append("default must be a non-negative integer")
        caps = value.get("by_institution_programme", {})
        if not isinstance(caps, dict) or not all(
            isinstance(k, str) and _is_count(v) for k, v in caps.items()
        ):
            errs.append("by_institution_programme must map 'INST|PROG|YEAR|SHIFT' to counts")
    elif key == "academic_year.admission_window":
        for year, w in value.items():
            try:
                start, end = date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
            except (TypeError, KeyError, ValueError):
                errs.append(f"{year}: start and end must be ISO dates")
                continue
            if start > end:
                errs.append(f"{year}: start is after end")
    elif key == "timeline.min_duration_tolerance_months":
        m = value.get("months")
        if not _is_count(m) or m > 24:
            errs.append("months must be an integer between 0 and 24")
    elif key == "validation.age_at_admission":
        for level, rng in value.items():
            if not (
                isinstance(rng, list)
                and len(rng) == 2
                and all(_is_count(x) for x in rng)
                and rng[0] <= rng[1]
            ):
                errs.append(f"{level}: expected [min_age, max_age]")
    elif key == "validation.apaar_suspicious":
        n = value.get("min_sequential_run")
        if not _is_count(n) or not 3 <= n <= 12:
            errs.append("min_sequential_run must be an integer between 3 and 12")
    elif key == "validation.name_match":
        n = value.get("min_score")
        if not isinstance(n, int | float) or isinstance(n, bool) or not 0 <= n <= 100:
            errs.append("min_score must be a number between 0 and 100")
    return errs
