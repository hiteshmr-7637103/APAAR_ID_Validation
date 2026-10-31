"""Validation message catalogue (spec 5.2: every outcome carries an i18n key).

English values only (spec 2.4); a Hindi catalogue can be added later under the same keys.
Institution-facing messages never mention another institution or another institution's
data (FR-9, FR-37); the neutral codes below exist for exactly that purpose.
"""

from __future__ import annotations

from typing import Any

# message_key -> English template (str.format placeholders).
MESSAGES_EN: dict[str, str] = {
    # FR-6
    "validation.apaar_invalid.missing": (
        "APAAR ID is missing. Every record must carry the student's 12-digit APAAR ID."
    ),
    "validation.apaar_invalid.format": (
        "APAAR ID '{value}' is not valid: it must be exactly 12 digits."
    ),
    "validation.apaar_suspicious": (
        "APAAR ID looks like a placeholder ({pattern}). Check it against the student's "
        "APAAR card."
    ),
    # FR-7
    "validation.apaar_not_found": (
        "This APAAR ID was not found in the APAAR registry (MOCK). Check the digits with "
        "the student."
    ),
    "validation.apaar_mismatch": (
        "The {what} does not match the APAAR registry (MOCK). Check it against the "
        "student's APAAR record."
    ),
    # FR-8
    "validation.dup_exact_same_inst": (
        "This student (same APAAR ID) is already recorded in {programme} for {year} at "
        "your institution (record #{other_record_id})."
    ),
    # FR-9 (neutral: no institution name or details)
    "validation.enrolment_elsewhere_neutral": (
        "A possible active enrolment exists elsewhere; please verify this student's details."
    ),
    # FR-14 / 9.1 transfer-aware rule (neutral)
    "validation.transfer_detected": (
        "An earlier enrolment for this APAAR ID ended on or before this admission date, so "
        "this is treated as a transfer. No action needed."
    ),
    # FR-10
    "validation.seat_over_cap": (
        "This admission exceeds the {bucket} seat bucket for {programme} ({year}): cap "
        "{cap}, used {used}, {excess} over. The record is stored, flagged and held for "
        "review by AICTE."
    ),
    # FR-3 dry run: projected Seat Ledger impact (nothing is reserved)
    "validation.seat_over_cap.projected": (
        "Row {row}: would exceed {bucket} by {excess} ({programme}, {year}: cap {cap}, "
        "{used} after this row). If submitted, the record is stored, flagged and held for "
        "review by AICTE."
    ),
    # FR-2 CSV parsing
    "validation.field_invalid.date": (
        "{label} '{value}' is not a valid date. Use the YYYY-MM-DD format."
    ),
    "validation.field_invalid.boolean": (
        "{label} '{value}' is not a valid yes/no value. Use true or false."
    ),
    "validation.field_invalid.too_long": "{label} is longer than {max} characters.",
    # FR-12
    "validation.fn_nationality_invalid": (
        "A foreign national (FN) record needs a nationality other than Indian."
    ),
    "validation.fn_id_document_missing": (
        "A foreign national (FN) record needs a passport or ID document note."
    ),
    "validation.oci_number_missing": "An OCI record needs the OCI card number.",
    "validation.nri_sponsor_missing": "An NRI record needs the sponsor's details.",
    "validation.admission_outside_window": (
        "Admission date {date} is outside the {year} admission window ({start} to {end}). "
        "Correct the date or add a justification."
    ),
    "validation.dob_implausible": (
        "Date of birth gives an age of {age} at admission, outside the plausible range "
        "{low}-{high} for {level} programmes."
    ),
    "validation.required_field_missing": "{label} is required.",
    "validation.field_invalid": "{label} has an invalid value '{value}'. Allowed: {allowed}.",
    "validation.programme_not_approved": (
        "Programme {programme} ({shift} shift) is not in your institution's approved list "
        "for {year}."
    ),
    # FR-13 / 9.5
    "validation.timeline_overlap": (
        "This full-time enrolment overlaps another full-time enrolment of the same student "
        "at your institution (record #{other_record_id}, {other_programme})."
    ),
    "validation.timeline_too_short.own": (
        "Graduation on {end} is earlier than admission ({admission}) plus the minimum "
        "duration of {years} years for {programme}."
    ),
    "validation.timeline_too_short.prior": (
        "The prior qualification used for this lateral entry (record #{other_record_id}) "
        "ended too soon after its admission to be a completed programme."
    ),
    "validation.timeline_lateral_no_prior": (
        "Lateral entry needs a completed diploma or degree on record for this APAAR ID; none "
        "was found. Attach proof of the prior qualification."
    ),
    "validation.timeline_invalid_date.future": "Admission date {date} is in the future.",
    "validation.timeline_invalid_date.end_before_admission": (
        "End date {end} is before the admission date {admission}."
    ),
    # Neutral counterpart of AICTE-only timeline cases
    "validation.timeline_check_neutral": (
        "This student's academic history needs verification; please check the prior "
        "qualification documents."
    ),
}


def render(message_key: str, **params: Any) -> str:
    template = MESSAGES_EN[message_key]
    return template.format(**params) if params else template
