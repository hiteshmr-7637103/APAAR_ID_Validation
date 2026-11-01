"""Pairwise feature extraction for student duplicate detection (spec 9.1).

Features extracted for a pair of candidate student records (A, B):
- Name similarities:
  - jaro_winkler: Jaro-Winkler distance on normalised names
  - token_sort_ratio: RapidFuzz token_sort_ratio / 100
  - token_set_ratio: RapidFuzz token_set_ratio / 100
  - initials_match: Initials compatibility (0.0 to 1.0)
  - phonetic_match: Boolean flag indicating identical Double Metaphone primary keys
  - phonetic_alt_match: Boolean flag indicating alternate Double Metaphone match
- DOB similarities:
  - dob_exact: 1.0 if exact date match, else 0.0
  - dob_day_month_swap: 1.0 if day and month are transposed (e.g. 2005-04-05 vs 2005-05-04)
  - dob_year_diff: absolute difference in birth years (clipped to 10) / 10.0
  - dob_days_diff: absolute difference in days (clipped to 365) / 365.0
- Demographic & Programme:
  - same_gender: 1.0 if gender matches, else 0.0 (0.5 if unknown)
  - same_programme: 1.0 if same programme canonical code, else 0.0
  - interval_overlap: 1.0 if enrolment periods overlap in calendar dates
- Privacy Tokens & Contact:
  - phone_match: 1.0 if contact_token (phone HMAC) matches, else 0.0
  - email_match: 1.0 if email_token matches, else 0.0
  - address_match: 1.0 if address_token matches, else 0.0
- APAAR ID:
  - apaar_equal: 1.0 if both non-empty and equal, else 0.0
  - apaar_missing: 1.0 if one or both APAAR IDs are missing / null, else 0.0
  - apaar_edit_dist: Levenshtein distance on 12-digit APAAR normalized (or 1.0 if missing)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from metaphone import doublemetaphone
from rapidfuzz import distance, fuzz

from app.services.names import initials_match

FEATURE_NAMES = (
    "jaro_winkler",
    "token_sort_ratio",
    "token_set_ratio",
    "initials_match",
    "phonetic_match",
    "phonetic_alt_match",
    "dob_exact",
    "dob_day_month_swap",
    "dob_year_diff",
    "dob_days_diff",
    "same_gender",
    "same_programme",
    "interval_overlap",
    "phone_match",
    "email_match",
    "address_match",
    "apaar_equal",
    "apaar_missing",
    "apaar_edit_dist",
)


@dataclass(frozen=True, slots=True)
class RecordEntity:
    """Lightweight representation of a record for feature extraction."""

    id: int
    institution_id: int
    apaar_id: str | None
    name_norm: str
    name_phonetic: str | None
    dob: date | None
    gender: str | None
    programme_code: str | None
    admission_date: date | None
    end_date: date | None
    contact_token: str | None
    email_token: str | None
    address_token: str | None


def is_day_month_swap(d1: date | None, d2: date | None) -> bool:
    if not d1 or not d2:
        return False
    if d1.year != d2.year:
        return False
    return d1.month == d2.day and d1.day == d2.month and d1.month != d1.day


def check_initials_compatible(name1: str, name2: str) -> float:
    """Calculate initials compatibility between two normalised names."""
    if not name1 or not name2:
        return 0.0
    if initials_match(name1, name2):
        return 1.0
    tokens1 = name1.split()
    tokens2 = name2.split()
    if len(tokens1) >= 2 and len(tokens2) >= 2:
        if tokens1[-1] == tokens2[-1] and tokens1[0][0] == tokens2[0][0]:
            return 0.90
    return 0.0


def check_interval_overlap(
    start1: date | None, end1: date | None, start2: date | None, end2: date | None
) -> float:
    """Returns 1.0 if admission intervals overlap, 0.0 otherwise."""
    if not start1 or not start2:
        return 1.0  # unknown, assume potential overlap
    # default end date if not provided: assume 4-year duration
    e1 = end1 or date(start1.year + 4, 6, 30)
    e2 = end2 or date(start2.year + 4, 6, 30)
    if start1 <= e2 and start2 <= e1:
        return 1.0
    return 0.0


def extract_pair_features(a: RecordEntity, b: RecordEntity) -> dict[str, float]:
    """Compute feature vector dictionary for a pair of candidate records."""
    # 1. Name features
    jw = distance.JaroWinkler.similarity(a.name_norm, b.name_norm)
    tsort = fuzz.token_sort_ratio(a.name_norm, b.name_norm) / 100.0
    tset = fuzz.token_set_ratio(a.name_norm, b.name_norm) / 100.0
    init_match = check_initials_compatible(a.name_norm, b.name_norm)

    # Phonetic keys
    p_match = 0.0
    p_alt_match = 0.0
    if a.name_phonetic and b.name_phonetic:
        if a.name_phonetic == b.name_phonetic:
            p_match = 1.0
    else:
        dm_a = doublemetaphone(a.name_norm)
        dm_b = doublemetaphone(b.name_norm)
        if dm_a[0] and dm_b[0] and dm_a[0] == dm_b[0]:
            p_match = 1.0
        elif (dm_a[1] and dm_a[1] == dm_b[0]) or (dm_b[1] and dm_a[0] == dm_b[1]):
            p_alt_match = 1.0

    # 2. DOB features
    dob_exact = 0.0
    dob_swap = 0.0
    dob_ydiff = 1.0
    dob_ddiff = 1.0
    if a.dob and b.dob:
        if a.dob == b.dob:
            dob_exact = 1.0
            dob_ydiff = 0.0
            dob_ddiff = 0.0
        else:
            if is_day_month_swap(a.dob, b.dob):
                dob_swap = 1.0
            ydiff = abs(a.dob.year - b.dob.year)
            dob_ydiff = min(ydiff, 10) / 10.0
            ddiff = abs((a.dob - b.dob).days)
            dob_ddiff = min(ddiff, 365) / 365.0

    # 3. Demographic & Programme
    same_gender = 0.5
    if a.gender and b.gender:
        same_gender = 1.0 if a.gender == b.gender else 0.0

    same_prog = 0.0
    if a.programme_code and b.programme_code:
        same_prog = 1.0 if a.programme_code == b.programme_code else 0.0

    overlap = check_interval_overlap(a.admission_date, a.end_date, b.admission_date, b.end_date)

    # 4. Privacy Tokens
    phone_match = 1.0 if a.contact_token and a.contact_token == b.contact_token else 0.0
    email_match = 1.0 if a.email_token and a.email_token == b.email_token else 0.0
    addr_match = 1.0 if a.address_token and a.address_token == b.address_token else 0.0

    # 5. APAAR ID
    apaar_eq = 0.0
    apaar_miss = 0.0
    apaar_dist = 1.0
    if not a.apaar_id or not b.apaar_id:
        apaar_miss = 1.0
    elif a.apaar_id == b.apaar_id:
        apaar_eq = 1.0
        apaar_dist = 0.0
    else:
        # Both present, different
        lev = distance.Levenshtein.distance(a.apaar_id, b.apaar_id)
        apaar_dist = min(lev, 12) / 12.0

    return {
        "jaro_winkler": round(jw, 4),
        "token_sort_ratio": round(tsort, 4),
        "token_set_ratio": round(tset, 4),
        "initials_match": round(init_match, 4),
        "phonetic_match": p_match,
        "phonetic_alt_match": p_alt_match,
        "dob_exact": dob_exact,
        "dob_day_month_swap": dob_swap,
        "dob_year_diff": round(dob_ydiff, 4),
        "dob_days_diff": round(dob_ddiff, 4),
        "same_gender": same_gender,
        "same_programme": same_prog,
        "interval_overlap": overlap,
        "phone_match": phone_match,
        "email_match": email_match,
        "address_match": addr_match,
        "apaar_equal": apaar_eq,
        "apaar_missing": apaar_miss,
        "apaar_edit_dist": round(apaar_dist, 4),
    }


def feature_vector(feat_dict: dict[str, float]) -> list[float]:
    """Convert feature dictionary to ordered float list."""
    return [float(feat_dict.get(name, 0.0)) for name in FEATURE_NAMES]
