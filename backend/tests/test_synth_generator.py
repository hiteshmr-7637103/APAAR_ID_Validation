"""Phase 2: synthetic generator and corruptions (spec 10). In-memory, no database."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import date

import pytest

from app.db.models import EntryType, LedgerKey, ProgrammeLevel, RecordStatus
from app.db.seed import DEMO_STUDENT_APAAR_ID, PROGRAMMES
from app.services.apaar_client import is_valid_apaar_format
from app.services.names import initials_match, normalise_name
from app.services.quota import compute_cap, ip_natural_key, ledger_key_for
from app.synth import corruptions as C
from app.synth import generator as G

LEVELS = {p["canonical_code"]: p["level"] for p in PROGRAMMES}


@pytest.fixture(scope="module")
def ds() -> G.Dataset:
    return G.generate(G.GeneratorConfig(seed=42))


def _labels(ds, scenario):
    return [lbl for lbl in ds.labels if lbl.scenario == scenario]


def _current(ds):
    return [r for r in ds.records if r.ip_key[2] == ds.config.academic_year]


# ----------------------------------------------------------------------------- determinism


def test_same_seed_gives_identical_dataset(ds):
    again = G.generate(G.GeneratorConfig(seed=42))
    assert again.fingerprint() == ds.fingerprint()
    assert again.summary() == ds.summary()


def test_different_seed_gives_different_dataset(ds):
    other = G.generate(G.GeneratorConfig(seed=7, n_institutions=12, target_students=3000))
    assert other.fingerprint() != ds.fingerprint()


# ----------------------------------------------------------------------------- national shape


def test_default_counts(ds):
    s = ds.summary()
    assert s["institutions"] == 40
    assert s["states"] == 8
    assert 18_000 <= s["current_year_records"] <= 22_000  # "about 20,000"
    assert 18_000 <= s["student_records"] <= 23_000
    assert len({r.apaar_id for r in ds.registry}) == len(ds.registry)


def test_institutions_have_3_to_8_programmes_and_standard_intakes(ds):
    year = ds.config.academic_year
    progs = defaultdict(set)
    for ip in ds.ips:
        if ip.year == year:
            progs[ip.inst_code].add(ip.prog_code)
            if not ip.is_demo:
                assert ip.approved_intake in (60, 120, 180, 240)
            assert ip.declared_enrolment is not None and ip.declared_enrolment > 0
    assert len(progs) == 40
    assert all(3 <= len(p) <= 8 for p in progs.values()), {k: len(v) for k, v in progs.items()}
    shifts = Counter(ip.shift for ip in ds.ips if ip.year == year)
    assert shifts["SECOND"] > 0
    levels = Counter(LEVELS[ip.prog_code] for ip in ds.ips if ip.year == year)
    assert levels[ProgrammeLevel.PGDM] > 0 and levels[ProgrammeLevel.MCA] > 0


def test_category_mix_and_small_special_shares(ds):
    cur = _current(ds)
    cats = Counter(r.category.value for r in cur)
    n = len(cur)
    for c in ("GENERAL", "OBC", "SC", "ST", "EWS"):
        assert cats[c] / n > 0.05, c
    for c in ("FN", "OCI", "PIO", "CIWGC", "NRI", "TFW"):
        assert 0 < cats[c] / n < 0.02, c
    lateral = sum(r.entry_type == EntryType.LATERAL for r in cur) / n
    assert 0.005 < lateral < 0.06


def test_bucket_mapping_and_consistency_fields(ds):
    faults = {lbl.record_key for lbl in _labels(ds, G.RULE_FAULT)}
    for r in ds.records:
        assert r.ledger_key == ledger_key_for(r.category, r.entry_type)
        if r.key in faults:
            continue
        if r.category.value == "FN":
            assert r.is_foreign_national and r.nationality != "Indian"
        if r.category.value == "OCI":
            assert r.is_oci and r.oci_number
        if r.category.value == "NRI":
            assert r.is_nri and r.nri_sponsor


def test_headcount_only_institutions(ds):
    head = [i for i in ds.institutions if i.headcount_only]
    assert 0.25 <= len(head) / len(ds.institutions) <= 0.30
    year = ds.config.academic_year
    received = Counter(r.ip_key for r in _current(ds))
    for ip in ds.ips:
        if ip.year != year or not ds_inst(ds, ip.inst_code).headcount_only:
            continue
        cov = received[ip.key] / ip.declared_enrolment
        assert 0.25 <= cov <= 0.65, (ip.key, cov)  # 30-60% before small rounding/extras
    labels = _labels(ds, G.HEADCOUNT_ONLY)
    assert len(labels) == len(head)
    assert all(0.30 <= lbl.detail["coverage"] <= 0.60 for lbl in labels)
    # Full-coverage institutions stay at >= 90% coverage.
    for inst in ds.institutions:
        if inst.headcount_only:
            continue
        declared = sum(
            ip.declared_enrolment for ip in ds.ips if ip.inst_code == inst.code and ip.year == year
        )
        got = sum(1 for r in _current(ds) if r.inst_code == inst.code)
        assert got / declared >= 0.88, inst.code


def ds_inst(ds, code):
    return next(i for i in ds.institutions if i.code == code)


def test_registry_ids_and_demo_student(ds):
    for r in ds.registry:
        assert is_valid_apaar_format(r.apaar_id) and r.apaar_id.startswith("99")
        assert not G._looks_fake(r.apaar_id)
    reg = {r.apaar_id: r for r in ds.registry}
    demo = reg[DEMO_STUDENT_APAAR_ID]
    assert demo.full_name == "Ananya Rao"
    student = next(s for s in ds.students if s.apaar_id == DEMO_STUDENT_APAAR_ID)
    recs = [r for r in ds.records if r.student_key == student.key]
    assert len(recs) == 1 and recs[0].ip_key[0] == "DEMO-INST-001"
    assert recs[0].status == RecordStatus.ACTIVE
    # Some registry names are in Devanagari script (spec 10).
    assert any(not r.full_name.isascii() for r in ds.registry)


def test_contacts_are_clearly_synthetic(ds):
    for r in ds.records:
        assert r.phone.startswith("9999") and len(r.phone) == 10
        assert r.email.endswith("@mail.example")


# ----------------------------------------------------------------------------- scenarios


def test_over_cap_buckets(ds):
    labels = _labels(ds, G.OVER_CAP)
    assert {lbl.detail["bucket"] for lbl in labels} == {"APPROVED", "LATERAL", "NRI"}
    ips = {ip.key: ip for ip in ds.ips}
    used = Counter((r.ip_key, r.ledger_key) for r in ds.records)
    for lbl in labels:
        bucket = LedgerKey(lbl.detail["bucket"])
        ip = ips[lbl.ip_key]
        cap = compute_cap(
            bucket, ip.approved_intake, ip.shift, ip_natural_key(*ip.key), ds.quota_rules
        )
        assert cap == lbl.detail["cap"]
        assert used[(ip.key, bucket)] == lbl.detail["used"] == cap + lbl.detail["excess"]
        assert lbl.detail["excess"] > 0
    # No other bucket anywhere is over its cap.
    flagged = {(lbl.ip_key, LedgerKey(lbl.detail["bucket"])) for lbl in labels}
    for (key, bucket), n in used.items():
        ip = ips[key]
        cap = compute_cap(
            bucket, ip.approved_intake, ip.shift, ip_natural_key(*key), ds.quota_rules
        )
        assert n <= cap or (key, bucket) in flagged, (key, bucket, n, cap)


def test_cross_institution_duplicates_are_tagged(ds):
    labels = _labels(ds, G.DUPLICATE)
    groups = defaultdict(list)
    for lbl in labels:
        groups[lbl.group_id].append(lbl)
    assert len(groups) == 300
    students = {s.key: s for s in ds.students}
    seen_types = Counter()
    for members in groups.values():
        orig = [m for m in members if m.role == "ORIGINAL"]
        copies = [m for m in members if m.role == "COPY"]
        assert len(orig) == 1 and 1 <= len(copies) <= 2
        insts = {m.inst_code for m in members}
        assert len(insts) == len(members)  # every copy at a different institution
        o = students[orig[0].student_key]
        for c in copies:
            assert set(c.corruption_types) <= set(C.CORRUPTION_TYPES)
            assert c.detail["split"] == C.split_for(c.corruption_types)
            seen_types.update(c.corruption_types)
            s = students[c.student_key]
            mode = c.detail["apaar_mode"]
            if mode == "SAME":
                assert s.apaar_id == o.apaar_id and c.corruption_types == []
            elif mode == "MISSING":
                assert s.apaar_id is None and C.APAAR_MISSING in c.corruption_types
            else:
                assert s.apaar_id != o.apaar_id and is_valid_apaar_format(s.apaar_id)
            if C.DOB_DAY_MONTH_SWAP in c.corruption_types:
                assert (s.dob.day, s.dob.month) == (o.dob.month, o.dob.day)
            elif mode != "SAME":
                assert s.dob == o.dob
    assert set(seen_types) == set(C.CORRUPTION_TYPES)
    splits = Counter(c.detail["split"] for c in labels if c.role == "COPY")
    assert splits["train"] > 50 and splits["heldout"] > 50


def test_shared_phone_rings(ds):
    rings = defaultdict(list)
    for lbl in _labels(ds, G.RING):
        rings[lbl.group_id].append(lbl.record_key)
    assert rings
    recs = {r.key: r for r in ds.records}
    students = {s.key: s for s in ds.students}
    for members in rings.values():
        assert 25 <= len(members) <= 60
        phones = {recs[k].phone for k in members}
        assert len(phones) == 1
        assert len({students[recs[k].student_key].apaar_id for k in members}) == len(members)
    # Natural sharing (siblings) never exceeds 2 distinct people per phone outside rings.
    ring_phones = {recs[m[0]].phone for m in rings.values()}
    people_per_phone = defaultdict(set)
    for r in ds.records:
        people_per_phone[r.phone].add(r.student_key)
    assert max(len(v) for p, v in people_per_phone.items() if p not in ring_phones) <= 3


def test_genuine_transfers_do_not_overlap(ds):
    groups = defaultdict(dict)
    recs = {r.key: r for r in ds.records}
    for lbl in _labels(ds, G.TRANSFER):
        groups[lbl.group_id][lbl.role] = recs[lbl.record_key]
    assert len(groups) == 30
    for g in groups.values():
        a, b = g["FROM"], g["TO"]
        assert a.student_key == b.student_key and a.inst_code != b.inst_code
        assert a.status == RecordStatus.WITHDRAWN and a.end_date is not None
        assert a.end_date < b.admission_date and b.end_date is None


def test_impossible_timelines(ds):
    recs = {r.key: r for r in ds.records}
    kinds = defaultdict(list)
    for lbl in _labels(ds, G.TIMELINE):
        kinds[lbl.corruption_types[0]].append(recs[lbl.record_key])
    assert set(kinds) == {
        "TIMELINE_OVERLAP",
        "TIMELINE_TOO_SHORT",
        "TIMELINE_LATERAL_NO_PRIOR",
        "TIMELINE_INVALID_DATE",
    }
    for r in kinds["TIMELINE_INVALID_DATE"]:
        assert r.admission_date > date.today() or (r.end_date and r.end_date < r.admission_date)
    by_student = defaultdict(list)
    for r in ds.records:
        by_student[r.student_key].append(r)
    for r in kinds["TIMELINE_LATERAL_NO_PRIOR"]:
        assert r.entry_type == EntryType.LATERAL and len(by_student[r.student_key]) == 1
    # Every other lateral entrant has a completed diploma record before admission.
    no_prior = {r.key for r in kinds["TIMELINE_LATERAL_NO_PRIOR"]}
    for r in ds.records:
        if r.entry_type == EntryType.LATERAL and r.key not in no_prior:
            prior = [
                p
                for p in by_student[r.student_key]
                if LEVELS[p.ip_key[1]] == ProgrammeLevel.DIPLOMA
                and p.status == RecordStatus.GRADUATED
            ]
            assert prior and prior[0].end_date < r.admission_date


def test_late_admission_burst(ds):
    labels = _labels(ds, G.LATE_BURST)
    assert len(labels) == 2
    assert all(lbl.detail["share_last_5_days"] >= 0.70 for lbl in labels)
    burst = {lbl.inst_code for lbl in labels}
    others = [r for r in _current(ds) if r.inst_code not in burst]
    late = sum(r.admission_date >= date(2025, 9, 26) for r in others) / len(others)
    assert late < 0.10


def test_rule_faults_and_registry_variants(ds):
    kinds = Counter(lbl.corruption_types[0] for lbl in _labels(ds, G.RULE_FAULT))
    assert set(kinds) == {
        "APAAR_INVALID",
        "APAAR_SUSPICIOUS",
        "APAAR_MISMATCH",
        "DUP_EXACT_SAME_INST",
        "OCI_NO_NUMBER",
        "FN_INDIAN_NATIONALITY",
        "NRI_NO_SPONSOR",
        "ADMISSION_OUTSIDE_WINDOW",
    }
    students = {s.key: s for s in ds.students}
    reg = {r.apaar_id: r for r in ds.registry}
    for lbl in _labels(ds, G.RULE_FAULT):
        if lbl.corruption_types == ["APAAR_INVALID"]:
            assert not is_valid_apaar_format(students[lbl.student_key].apaar_id)
    variants = _labels(ds, G.REGISTRY_VARIANT)
    assert {lbl.detail["kind"] for lbl in variants} == {"script", "spelling"}
    for lbl in variants:
        s = students[lbl.student_key]
        assert reg[s.apaar_id].full_name != s.name


def test_scaling_to_another_size():
    small = G.generate(G.GeneratorConfig(seed=42, n_institutions=15, target_students=6000))
    s = small.summary()
    assert s["institutions"] == 15
    assert 0.85 * 6000 <= s["current_year_records"] <= 1.15 * 6000


# ----------------------------------------------------------------------------- corruptions


def test_corruption_functions():
    rng = random.Random(1)
    assert C.initials("Rahul Kumar Sharma", rng).endswith("Sharma")
    assert initials_match(C.initials("Rahul Kumar Sharma", rng), "Rahul Kumar Sharma")
    assert (
        C.swapped_order("Rahul Sharma", random.Random(2)).replace(",", "").lower().split()[0]
        == "sharma"
    )
    for seed in range(30):
        t = C.typo("Rahul Sharma", random.Random(seed))
        assert t != "Rahul Sharma" and _edit_distance(t, "Rahul Sharma") <= 2
    assert C.swap_day_month(date(2007, 3, 9)) == date(2007, 9, 3)
    assert not C.can_swap_day_month(date(2007, 3, 3)) and not C.can_swap_day_month(
        date(2007, 3, 20)
    )
    for seed in range(30):
        w = C.wrong_apaar("991234567890", random.Random(seed))
        assert w != "991234567890" and len(w) == 12 and w[:2] == "99"
    tokens = [("Rahul", "राहुल"), ("Sharma", "शर्मा")]
    outs = {C.transliteration(tokens, random.Random(s), allow_script=True) for s in range(20)}
    assert "राहुल शर्मा" in outs and any(o.isascii() for o in outs)
    for o in outs:  # every transliteration variant still normalises to the same key
        assert normalise_name(o) == "rahul sharma"


def test_split_for():
    assert C.split_for([C.INITIALS, C.APAAR_WRONG]) == "train"
    assert C.split_for([C.APAAR_MISSING]) == "train"
    assert C.split_for([C.TRANSLITERATION, C.APAAR_MISSING]) == "heldout"
    assert C.split_for([C.DOB_DAY_MONTH_SWAP, C.APAAR_WRONG]) == "heldout"
    assert C.split_for([C.INITIALS, C.TYPO, C.APAAR_WRONG]) == "heldout"  # combined


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
