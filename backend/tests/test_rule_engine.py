"""Rule engine unit tests (spec 13: one pass and one fail test per code in 5.2 and 9.5).

Pure tests over `MemoryRuleContext`; no database. The Seat Ledger code SEAT_OVER_CAP is
tested against PostgreSQL in test_seat_ledger.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.db.models import CaseType, CaseVisibility, LedgerKey, Severity
from app.services.apaar_client import ApaarRecord
from app.services.messages import MESSAGES_EN
from app.services.names import name_similarity
from app.services.quota import ledger_key_for
from app.services.rule_engine import (
    RULE_CODES,
    IPView,
    MemoryRuleContext,
    RecordInput,
    suggest_programmes,
    suspicious_pattern,
    validate_record,
)
from app.services.timeline import EnrolmentView
from app.services.transfer import Relation, relation

TODAY = date(2026, 1, 15)
APAAR = "990000000017"
OWN, OTHER = 10, 20

IP_UG = IPView(1, OWN, "I-OWN", "UG-CSE", "B.Tech CSE", "UG", 4, "2025-26", "REGULAR", 120)
IP_UG_SECOND = replace(IP_UG, id=5, shift="SECOND", approved_intake=60)
IP_OWN_ME = IPView(6, OWN, "I-OWN", "UG-ME", "B.Tech ME", "UG", 4, "2024-25", "REGULAR", 60)
IP_OTHER_UG = IPView(2, OTHER, "I-OTHER", "UG-CSE", "B.Tech CSE", "UG", 4, "2025-26", "REGULAR", 60)
IP_OTHER_DIP = IPView(
    3, OTHER, "I-OTHER", "DIP-CE", "Diploma CE", "DIPLOMA", 3, "2022-23", "REGULAR", 60
)
IP_OTHER_PRIOR = IPView(
    4, OTHER, "I-OTHER", "UG-ME", "B.Tech ME", "UG", 4, "2024-25", "REGULAR", 60
)
IPS = [IP_UG, IP_UG_SECOND, IP_OWN_ME, IP_OTHER_UG, IP_OTHER_DIP, IP_OTHER_PRIOR]

REGISTRY = {APAAR: ApaarRecord(APAAR, "Rahul Kumar Sharma", date(2007, 3, 1), "M", "MOCK")}
for extra in ("111111111111", "991234567890", "998765432100"):
    REGISTRY[extra] = ApaarRecord(extra, "Rahul Kumar Sharma", date(2007, 3, 1), "M", "MOCK")


def good(**kw) -> RecordInput:
    base = dict(
        institution_id=OWN,
        apaar_id=APAAR,
        full_name="Rahul Kumar Sharma",
        dob=date(2007, 3, 1),
        gender="M",
        programme_code="UG-CSE",
        shift="REGULAR",
        academic_year="2025-26",
        admission_date=date(2025, 8, 1),
        admission_category="GENERAL",
        entry_type="REGULAR",
        mode="FULL_TIME",
        nationality="Indian",
    )
    base.update(kw)
    return RecordInput(**base)


def enrol(rid, ip: IPView, adm, end=None, status="ACTIVE", mode="FULL_TIME", entry="REGULAR"):
    return EnrolmentView(
        record_id=rid,
        student_id=1,
        institution_id=ip.institution_id,
        institution_programme_id=ip.id,
        institution_code=ip.institution_code,
        programme_code=ip.programme_code,
        level=ip.level,
        duration_years=ip.duration_years,
        academic_year=ip.academic_year,
        admission_date=adm,
        end_date=end,
        status=status,
        mode=mode,
        entry_type=entry,
    )


def ctx(*enrolments: EnrolmentView) -> MemoryRuleContext:
    return MemoryRuleContext(IPS, {APAAR: list(enrolments)}, REGISTRY, today=TODAY)


def run(inp: RecordInput, context: MemoryRuleContext | None = None):
    return validate_record(inp, context or ctx())


def codes(report) -> list[str]:
    return report.codes


# Ongoing full-time UG at another institution, current year (same-year duplicate).
OTHER_SAME_YEAR = enrol(101, IP_OTHER_UG, date(2025, 7, 20))
# Withdrew from the other institution before this admission (genuine transfer).
OTHER_WITHDRAWN = enrol(102, IP_OTHER_UG, date(2025, 7, 5), date(2025, 7, 25), "WITHDRAWN")
# Ongoing UG from the previous year at another institution (impossible overlap).
OTHER_PRIOR_ONGOING = enrol(103, IP_OTHER_PRIOR, date(2024, 8, 1))
# Completed diploma (valid prior for lateral entry) and a too-short one.
DIPLOMA_DONE = enrol(104, IP_OTHER_DIP, date(2022, 8, 1), date(2025, 6, 10), "GRADUATED")
DIPLOMA_SHORT = enrol(
    105,
    replace(IP_OTHER_DIP, academic_year="2024-25"),
    date(2024, 8, 1),
    date(2025, 5, 20),
    "GRADUATED",
)


# --------------------------------------------------------------------------- table


# code -> (failing input, context). Each must emit `code`; `good()` emits nothing.
FAIL_CASES = {
    "APAAR_INVALID": (good(apaar_id="12345"), ctx()),
    "APAAR_SUSPICIOUS": (good(apaar_id="111111111111"), ctx()),
    "APAAR_NOT_FOUND": (good(apaar_id="990000000099"), ctx()),
    "APAAR_MISMATCH": (good(dob=date(2006, 3, 1)), ctx()),
    "DUP_EXACT_SAME_INST": (good(), ctx(enrol(100, IP_UG, date(2025, 7, 10)))),
    "ENROLMENT_ELSEWHERE_NEUTRAL": (good(), ctx(OTHER_SAME_YEAR)),
    "TRANSFER_DETECTED": (good(), ctx(OTHER_WITHDRAWN)),
    "FN_NATIONALITY_INVALID": (
        good(admission_category="FN", is_foreign_national=True, id_document_note="P123"),
        ctx(),
    ),
    "FN_ID_DOCUMENT_MISSING": (
        good(admission_category="FN", is_foreign_national=True, nationality="Nepal"),
        ctx(),
    ),
    "OCI_NUMBER_MISSING": (
        good(admission_category="OCI", is_oci=True, nationality="Canada"),
        ctx(),
    ),
    "NRI_SPONSOR_MISSING": (good(admission_category="NRI", is_nri=True), ctx()),
    "ADMISSION_OUTSIDE_WINDOW": (good(admission_date=date(2025, 10, 20)), ctx()),
    "DOB_IMPLAUSIBLE": (good(dob=date(2015, 3, 1)), ctx()),
    "REQUIRED_FIELD_MISSING": (good(full_name=None), ctx()),
    "FIELD_INVALID": (good(admission_category="XYZ"), ctx()),
    "PROGRAMME_NOT_APPROVED": (good(programme_code="UG-ECE"), ctx()),
    "TIMELINE_OVERLAP": (good(), ctx(enrol(106, IP_OWN_ME, date(2024, 8, 1)))),
    "TIMELINE_TOO_SHORT": (
        good(
            programme_code="UG-CSE",
            admission_date=date(2021, 8, 1),
            academic_year="2025-26",
            end_date=date(2022, 5, 30),
            status="GRADUATED",
        ),
        ctx(),
    ),
    "TIMELINE_LATERAL_NO_PRIOR": (good(entry_type="LATERAL"), ctx()),
    "TIMELINE_INVALID_DATE": (good(admission_date=date(2030, 1, 15)), ctx()),
    "TIMELINE_CHECK_NEUTRAL": (
        good(entry_type="LATERAL"),
        ctx(DIPLOMA_SHORT),
    ),
}
# Codes not emitted by validate_record() itself (tested elsewhere).
LEDGER_CODES = {"SEAT_OVER_CAP"}


def test_every_rule_code_has_a_fail_test_and_a_message():
    assert set(FAIL_CASES) | LEDGER_CODES == set(RULE_CODES)
    for code in RULE_CODES:
        assert any(k.startswith(f"validation.{code.lower()}") for k in MESSAGES_EN), code


def test_clean_record_passes_every_rule():
    report = run(good())
    assert report.outcomes == [] and report.cases == []
    assert not report.has_error
    assert report.ledger_key == LedgerKey.APPROVED
    assert report.apaar_status == {
        "source": "MOCK",
        "format_ok": True,
        "suspicious": False,
        "found": True,
        "name_match": True,
        "dob_match": True,
    }


@pytest.mark.parametrize("code", sorted(FAIL_CASES))
def test_rule_fails(code):
    inp, context = FAIL_CASES[code]
    report = run(inp, context)
    hits = [o for o in report.outcomes if o.code == code]
    assert hits, f"{code} not raised: {report.codes}"
    for o in hits:
        assert o.severity == RULE_CODES[code]
        assert o.message_key.startswith(f"validation.{code.lower()}")
        assert o.message_en and "{" not in o.message_en
    assert report.has_error == any(o.severity == Severity.ERROR for o in report.outcomes)


@pytest.mark.parametrize("code", sorted(FAIL_CASES))
def test_rule_passes_on_clean_record(code):
    assert code not in codes(run(good()))


# --------------------------------------------------------------------------- FR-6 / FR-7


def test_apaar_missing_and_format_variants():
    for bad in (None, "", "99027483165", "99027483165X", "9902748316501", "99 0274 8316 50"):
        report = run(good(apaar_id=bad))
        assert codes(report)[:1] == ["APAAR_INVALID"], bad
        assert report.has_error and report.apaar_status["format_ok"] is False
    assert run(good(apaar_id=None)).outcomes[0].message_key == "validation.apaar_invalid.missing"


def test_suspicious_patterns():
    assert suspicious_pattern("111111111111") == "all digits the same"
    assert suspicious_pattern("991234567890") == "sequential digits"
    assert suspicious_pattern("998765432100") == "sequential digits"
    for normal in ("990274831650", APAAR, "991212121212"):
        assert suspicious_pattern(normal) is None, normal
    # suspicious is a WARNING, not an ERROR: the ID is still checked against the registry
    report = run(good(apaar_id="991234567890"))
    assert codes(report) == ["APAAR_SUSPICIOUS"] and not report.has_error


@pytest.mark.parametrize(
    "variant",
    [
        "R. K. Sharma",
        "Sharma Rahul Kumar",
        "राहुल कुमार शर्मा",
        "Rahul Sharma",
        "RAHUL KUMAR SARMA",
    ],
)
def test_registry_name_check_is_transliteration_aware(variant):
    report = run(good(full_name=variant))
    assert "APAAR_MISMATCH" not in codes(report), (variant, name_similarity(variant, "x"))


def test_registry_mismatch_reports_booleans_not_registry_data():
    report = run(good(full_name="Priya Nair", dob=date(2006, 3, 1)))
    mism = next(o for o in report.outcomes if o.code == "APAAR_MISMATCH")
    assert mism.severity == Severity.WARNING
    assert "name and date of birth" in mism.message_en
    blob = repr(mism.to_dict()) + repr(report.apaar_status)
    assert "Rahul" not in blob and "2007-03-01" not in blob
    assert report.apaar_status["name_match"] is False and report.apaar_status["dob_match"] is False


# --------------------------------------------------------------------------- FR-8 / FR-9


def test_same_institution_duplicate_only_flags_the_later_record():
    first = enrol(100, IP_UG, date(2025, 7, 10))
    # replaying the earlier record against the later one: no duplicate on the original
    replay_first = good(record_id=100, institution_programme_id=IP_UG.id)
    later = enrol(200, IP_UG, date(2025, 7, 10))
    assert "DUP_EXACT_SAME_INST" not in codes(run(replay_first, ctx(first, later)))
    replay_later = good(record_id=200, institution_programme_id=IP_UG.id)
    assert "DUP_EXACT_SAME_INST" in codes(run(replay_later, ctx(first, later)))


def test_cross_institution_duplicate_is_aicte_only_and_neutral_for_institution():
    report = run(good(), ctx(OTHER_SAME_YEAR))
    assert len(report.cases) == 1
    case = report.cases[0]
    assert case.type == CaseType.DUP_EXACT_CROSS and case.visibility == CaseVisibility.AICTE_ONLY
    assert {e["institution_code"] for e in case.enrolments} == {"I-OWN", "I-OTHER"}
    assert APAAR not in case.key  # dedupe key uses the HMAC token, not the raw ID
    neutral = [o for o in report.outcomes if o.code == "ENROLMENT_ELSEWHERE_NEUTRAL"]
    assert len(neutral) == 1 and neutral[0].severity == Severity.INFO
    assert neutral[0].message_en == (
        "A possible active enrolment exists elsewhere; please verify this student's details."
    )
    institution_view = repr([o.to_dict() for o in report.outcomes])
    for leak in ("I-OTHER", "101", str(OTHER), "2025-07-20"):
        assert leak not in institution_view, leak
    assert not report.has_error  # flagged for AICTE, not blocked


def test_rejected_enrolments_are_ignored():
    rejected = replace(OTHER_SAME_YEAR, status="REJECTED")
    assert run(good(), ctx(rejected)).cases == []


# --------------------------------------------------------------------------- transfer-aware


def test_genuine_transfer_gives_info_and_no_case():
    report = run(good(), ctx(OTHER_WITHDRAWN))
    assert codes(report) == ["TRANSFER_DETECTED"]
    assert report.cases == [] and report.outcomes[0].severity == Severity.INFO


def test_transfer_on_the_same_day_counts_as_transfer():
    same_day = replace(OTHER_WITHDRAWN, end_date=date(2025, 8, 1))
    assert codes(run(good(), ctx(same_day))) == ["TRANSFER_DETECTED"]


def test_overlapping_enrolment_is_not_a_transfer_and_opens_a_case():
    still_there = replace(OTHER_WITHDRAWN, end_date=date(2025, 8, 20))  # left after we admitted
    report = run(good(), ctx(still_there))
    assert "TRANSFER_DETECTED" not in codes(report)
    assert [c.type for c in report.cases] == [CaseType.DUP_EXACT_CROSS]


def test_relation_classification():
    new = enrol(None, IP_UG, date(2025, 8, 1))
    assert relation(new, OTHER_WITHDRAWN) == Relation.TRANSFER
    assert relation(new, DIPLOMA_DONE) == Relation.PRIOR_COMPLETED
    assert relation(new, OTHER_SAME_YEAR) == Relation.OVERLAP
    left_early = enrol(None, IP_UG, date(2025, 7, 1), date(2025, 7, 10), "WITHDRAWN")
    assert relation(left_early, OTHER_SAME_YEAR) == Relation.LATER
    old = enrol(9, IP_OTHER_DIP, date(2018, 8, 1))  # projected end 2021: no overlap
    assert relation(new, old) == Relation.DISJOINT


# --------------------------------------------------------------------------- 9.5 timelines


def test_cross_institution_overlap_is_an_aicte_only_timeline_case():
    report = run(good(), ctx(OTHER_PRIOR_ONGOING))
    assert [c.type for c in report.cases] == [CaseType.TIMELINE]
    assert report.cases[0].code == "TIMELINE_OVERLAP"
    assert codes(report) == ["ENROLMENT_ELSEWHERE_NEUTRAL"]


def test_overlap_needs_two_full_time_enrolments():
    part_time = replace(OTHER_PRIOR_ONGOING, mode="PART_TIME")
    assert run(good(), ctx(part_time)).cases == []
    own_part_time = enrol(106, IP_OWN_ME, date(2024, 8, 1), mode="PART_TIME")
    assert "TIMELINE_OVERLAP" not in codes(run(good(), ctx(own_part_time)))


def test_too_short_own_record_passes_with_full_duration():
    ok = good(admission_date=date(2021, 8, 1), end_date=date(2025, 5, 30), status="GRADUATED")
    assert "TIMELINE_TOO_SHORT" not in codes(run(ok))


def test_lateral_entry_with_completed_diploma_passes():
    report = run(good(entry_type="LATERAL"), ctx(DIPLOMA_DONE))
    assert report.outcomes == [] and report.cases == []
    assert report.ledger_key == LedgerKey.LATERAL


def test_lateral_entry_on_a_too_short_prior_elsewhere():
    report = run(good(entry_type="LATERAL"), ctx(DIPLOMA_SHORT))
    assert [(c.type, c.code) for c in report.cases] == [(CaseType.TIMELINE, "TIMELINE_TOO_SHORT")]
    assert codes(report) == ["TIMELINE_CHECK_NEUTRAL"]
    assert "I-OTHER" not in repr([o.to_dict() for o in report.outcomes])


def test_invalid_dates():
    future = run(good(admission_date=date(2030, 1, 15)))
    assert "TIMELINE_INVALID_DATE" in codes(future) and future.has_error
    backwards = run(good(end_date=date(2025, 7, 1), status="WITHDRAWN"))
    inv = [o for o in backwards.outcomes if o.code == "TIMELINE_INVALID_DATE"]
    assert len(inv) == 1 and inv[0].field == "end_date"
    assert "TIMELINE_INVALID_DATE" not in codes(run(good(end_date=date(2025, 12, 1))))


# --------------------------------------------------------------------------- FR-12


def test_consistency_checks_pass_when_complete():
    complete = [
        good(
            admission_category="FN",
            is_foreign_national=True,
            nationality="Nepal",
            id_document_note="Passport N1234567",
        ),
        good(admission_category="OCI", is_oci=True, nationality="Canada", oci_number="A1234567"),
        good(admission_category="NRI", is_nri=True, nri_sponsor="Uncle, UAE"),
        good(admission_date=date(2025, 7, 1)),
        good(admission_date=date(2025, 9, 30)),
    ]
    for inp in complete:
        assert run(inp).outcomes == [], inp


def test_flags_alone_trigger_category_requirements():
    assert "NRI_SPONSOR_MISSING" in codes(run(good(is_nri=True)))
    assert "OCI_NUMBER_MISSING" in codes(run(good(is_oci=True)))
    assert "FN_NATIONALITY_INVALID" in codes(run(good(is_foreign_national=True)))


def test_programme_must_be_approved_for_that_shift_with_suggestions():
    wrong_shift = run(good(shift="PART_TIME"))
    assert "PROGRAMME_NOT_APPROVED" in codes(wrong_shift)
    fix = next(o for o in wrong_shift.outcomes if o.code == "PROGRAMME_NOT_APPROVED").suggested_fix
    assert {p["programme_code"] for p in fix["programmes"]} == {"UG-CSE"}
    assert run(good(shift="SECOND")).outcomes == []
    assert [p["programme_code"] for p in suggest_programmes("ug-c", [IP_UG])] == ["UG-CSE"]
    assert suggest_programmes("computer", [IP_OTHER_DIP]) == []


def test_required_fields_and_enum_values():
    report = run(good(gender=None, mode="SOMETIMES", dob=None))
    by_field = {(o.code, o.field) for o in report.outcomes}
    assert ("REQUIRED_FIELD_MISSING", "gender") in by_field
    assert ("REQUIRED_FIELD_MISSING", "dob") in by_field
    assert ("FIELD_INVALID", "mode") in by_field
    assert report.has_error


def test_rule_values_come_from_rules_data():
    """Rules-as-data: a stricter window or age range changes outcomes with no code change."""
    rules = ctx().rules
    rules["academic_year.admission_window"]["2025-26"] = {
        "start": "2025-08-15",
        "end": "2025-09-30",
    }
    rules["validation.age_at_admission"]["UG"] = [19, 30]
    strict = MemoryRuleContext(IPS, {}, REGISTRY, today=TODAY, rules=rules)
    assert {"ADMISSION_OUTSIDE_WINDOW", "DOB_IMPLAUSIBLE"} <= set(codes(run(good(), strict)))


# --------------------------------------------------------------------------- buckets


@pytest.mark.parametrize(
    "category,entry,bucket",
    [
        ("GENERAL", "REGULAR", LedgerKey.APPROVED),
        ("SC", "REGULAR", LedgerKey.APPROVED),
        ("EWS", "REGULAR", LedgerKey.APPROVED),
        ("FN", "REGULAR", LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC),
        ("OCI", "REGULAR", LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC),
        ("PIO", "REGULAR", LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC),
        ("CIWGC", "REGULAR", LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC),
        ("TFW", "REGULAR", LedgerKey.SUPERNUMERARY_TFW),
        ("NRI", "REGULAR", LedgerKey.NRI),
        ("FN", "LATERAL", LedgerKey.LATERAL),  # LATERAL wins (first match)
        ("NRI", "LATERAL", LedgerKey.LATERAL),
        ("GENERAL", "LATERAL", LedgerKey.LATERAL),
    ],
)
def test_bucket_mapping(category, entry, bucket):
    assert ledger_key_for(category, entry) == bucket
