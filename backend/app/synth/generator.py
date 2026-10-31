"""Deterministic synthetic national dataset (spec 10).

`generate(GeneratorConfig(seed=42))` builds everything in memory (no DB access) so the
result can be inspected and tested; `app/synth/loader.py` writes it to PostgreSQL.

Produces, for the current academic year (default 2025-26):
- `n_institutions` institutions (the 3 Phase 1 demo institutions + synthetic ones, codes
  `SYN-INST-nnn`) across 8 states, 3-8 programmes each, intakes 60/120/180/240, second-shift
  and part-time rows, a PGDM/MCA mix, and `declared_enrolment` per programme;
- about `target_students` students with a realistic category mix, small shares of
  FN/OCI/PIO/CIWGC/NRI/TFW and lateral entries;
- 25-30% headcount-only institutions (records cover only 30-60% of declared enrolment);
- the MOCK APAAR registry (synthetic "99"-prefixed IDs; some names in Devanagari or other
  romanised spellings);
- injected scenarios with ground-truth labels: over-cap buckets, cross-institution
  duplicates with tagged corruptions, shared-phone rings, impossible timelines, a late
  admission burst, headcount-only institutions, genuine transfers, plus small rule faults
  and hard negatives.

Records are written as historical, already-approved data (status ACTIVE/WITHDRAWN/
GRADUATED, trust tier 0, no Seat Ledger rows); later phases replay them through the rule
engine, Seat Ledger and trust tiers (see docs/decisions.md).
"""

from __future__ import annotations

import hashlib
import random
import string
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.db import seed as phase1
from app.db.models import AdmissionCategory as Cat
from app.db.models import EntryType, LedgerKey, ProgrammeLevel, RecordStatus, Shift, StudyMode
from app.services.quota import compute_cap, default_rule_values, ip_natural_key, ledger_key_for
from app.synth import corruptions as C
from app.synth.pools import (
    FOREIGN_NATIONALS,
    LOCALITIES,
    NAMES,
    NRI_COUNTRIES,
    NRI_RELATIONS,
    OCI_COUNTRIES,
    PIO_COUNTRIES,
    STATES,
)

IPKey = tuple[str, str, str, str]  # (institution code, programme code, academic year, shift)
Token = tuple[str, str | None]

# Scenario names stored in ground_truth_labels.scenario
OVER_CAP = "OVER_CAP"
DUPLICATE = "DUPLICATE"
RING = "RING"
TIMELINE = "TIMELINE"
LATE_BURST = "LATE_BURST"
HEADCOUNT_ONLY = "HEADCOUNT_ONLY"
TRANSFER = "TRANSFER"
RULE_FAULT = "RULE_FAULT"
REGISTRY_VARIANT = "REGISTRY_VARIANT"
NATURAL_SHARED_PHONE = "NATURAL_SHARED_PHONE"

DEMO_KINDS = {
    "DEMO-INST-001": "ENGINEERING",
    "DEMO-INST-002": "MANAGEMENT",
    "DEMO-INST-003": "POLYTECHNIC",
}
KIND_LABELS = {
    "ENGINEERING": "Engineering College",
    "MANAGEMENT": "School of Management",
    "POLYTECHNIC": "Polytechnic",
    "COMPUTER": "Institute of Computer Applications",
}
KIND_WEIGHTS = [
    ("ENGINEERING", 0.50),
    ("MANAGEMENT", 0.20),
    ("POLYTECHNIC", 0.17),
    ("COMPUTER", 0.13),
]
PROGRAMME_POOLS = {
    "ENGINEERING": (
        ["UG-CSE"],
        ["UG-ECE", "UG-ME", "UG-CE", "UG-EE", "UG-IT", "UG-AIDS", "PG-MTECH-CSE", "MCA", "PG-MBA"],
        (3, 8),
    ),
    "MANAGEMENT": (
        ["PGDM-GEN"],
        ["PGDM-FIN", "PGDM-MKT", "PGDM-HR", "PGDM-BA", "PGDM-IB", "PG-MBA", "MCA"],
        (3, 6),
    ),
    "POLYTECHNIC": ([], ["DIP-CE", "DIP-EE", "DIP-ME", "DIP-CSE", "DIP-ECE", "DIP-AUTO"], (3, 6)),
    "COMPUTER": (
        ["MCA"],
        ["PG-MBA", "PGDM-GEN", "PGDM-BA", "PG-MTECH-CSE", "UG-CSE", "UG-IT"],
        (3, 5),
    ),
}
INTAKES = [60, 120, 180, 240]
INTAKE_WEIGHTS = [0.35, 0.35, 0.20, 0.10]
APPROVED_CATEGORY_MIX = [
    (Cat.GENERAL, 0.38),
    (Cat.OBC, 0.27),
    (Cat.SC, 0.15),
    (Cat.ST, 0.07),
    (Cat.EWS, 0.10),
    (Cat.OTHER, 0.03),
]
SUPERNUMERARY_MIX = [(Cat.FN, 0.45), (Cat.OCI, 0.20), (Cat.PIO, 0.10), (Cat.CIWGC, 0.25)]
FEMALE_SHARE = {
    ProgrammeLevel.UG: 0.30,
    ProgrammeLevel.DIPLOMA: 0.20,
    ProgrammeLevel.PG: 0.35,
    ProgrammeLevel.PGDM: 0.42,
    ProgrammeLevel.MCA: 0.40,
    ProgrammeLevel.OTHER: 0.40,
}
# Age at admission (years) by level / entry type
AGE_RANGE = {
    ProgrammeLevel.UG: (17, 19),
    ProgrammeLevel.DIPLOMA: (15, 17),
    ProgrammeLevel.PG: (21, 26),
    ProgrammeLevel.PGDM: (21, 26),
    ProgrammeLevel.MCA: (21, 25),
    ProgrammeLevel.OTHER: (18, 25),
}
LATERAL_AGE = (19, 21)

DEMO_STUDENT = dict(
    apaar_id=phase1.DEMO_STUDENT_APAAR_ID,
    tokens=[("Ananya", "अनन्या"), ("Rao", None)],
    dob=date(2007, 5, 14),
    gender="F",
    ip=("DEMO-INST-001", "UG-CSE", phase1.ACADEMIC_YEAR, Shift.REGULAR.value),
    admission_date=date(2025, 8, 4),
)


@dataclass
class GeneratorConfig:
    seed: int = 42
    n_institutions: int = 40
    target_students: int = 20000
    academic_year: str = phase1.ACADEMIC_YEAR
    headcount_only_share: float = 0.275

    def scaled(self, per_20k: float, minimum: int = 1) -> int:
        return max(minimum, round(per_20k * self.target_students / 20000))


@dataclass
class InstitutionRow:
    code: str
    name: str
    state: str
    district: str
    type: str
    kind: str
    is_demo: bool
    headcount_only: bool = False
    late_burst: bool = False


@dataclass
class IPRow:
    key: IPKey
    approved_intake: int
    declared_enrolment: int | None
    is_demo: bool

    @property
    def inst_code(self) -> str:
        return self.key[0]

    @property
    def prog_code(self) -> str:
        return self.key[1]

    @property
    def year(self) -> str:
        return self.key[2]

    @property
    def shift(self) -> str:
        return self.key[3]


@dataclass
class StudentRow:
    key: int
    apaar_id: str | None
    name: str  # as submitted by the institution
    dob: date
    gender: str


@dataclass
class RecordRow:
    key: int
    student_key: int
    ip_key: IPKey
    student_college_id: str
    admission_date: date
    end_date: date | None
    category: Cat
    entry_type: EntryType
    mode: StudyMode
    ledger_key: LedgerKey
    nationality: str
    is_nri: bool
    nri_sponsor: str | None
    is_oci: bool
    oci_number: str | None
    is_foreign_national: bool
    status: RecordStatus
    phone: str | None
    email: str | None
    address: str | None
    id_document_note: str | None = None

    @property
    def inst_code(self) -> str:
        return self.ip_key[0]


@dataclass
class RegistryRow:
    apaar_id: str
    full_name: str
    dob: date
    gender: str


@dataclass
class LabelRow:
    scenario: str
    group_id: str
    role: str | None = None
    corruption_types: list[str] = field(default_factory=list)
    record_key: int | None = None
    student_key: int | None = None
    inst_code: str | None = None
    ip_key: IPKey | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Person:
    student_key: int
    apaar_id: str
    tokens: list[Token]
    dob: date
    gender: str
    region: str
    home_state: str

    @property
    def latin(self) -> str:
        return " ".join(t for t, _ in self.tokens)


@dataclass
class Dataset:
    config: GeneratorConfig
    institutions: list[InstitutionRow]
    ips: list[IPRow]
    students: list[StudentRow]
    records: list[RecordRow]
    registry: list[RegistryRow]
    labels: list[LabelRow]
    quota_rules: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        year = self.config.academic_year
        current = [r for r in self.records if r.ip_key[2] == year]
        return {
            "seed": self.config.seed,
            "academic_year": year,
            "institutions": len(self.institutions),
            "states": len({i.state for i in self.institutions}),
            "institution_programmes": len(self.ips),
            "students": len(self.students),
            "student_records": len(self.records),
            "current_year_records": len(current),
            "registry_entries": len(self.registry),
            "headcount_only_institutions": sum(i.headcount_only for i in self.institutions),
            "labels": dict(sorted(Counter(lbl.scenario for lbl in self.labels).items())),
        }

    def fingerprint(self) -> str:
        """Stable hash of the generated content (determinism checks)."""
        h = hashlib.sha256()
        for part in (self.institutions, self.ips, self.students, self.records, self.registry):
            for row in part:
                h.update(repr(row).encode())
        for lbl in self.labels:
            h.update(repr(lbl).encode())
        return h.hexdigest()


def _weighted(rng: random.Random, pairs):
    items, weights = zip(*pairs, strict=True)
    return rng.choices(items, weights=weights, k=1)[0]


class _Generator:
    def __init__(self, cfg: GeneratorConfig) -> None:
        if cfg.n_institutions < len(phase1.INSTITUTIONS) + 1:
            raise ValueError("n_institutions must exceed the number of demo institutions")
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.year = cfg.academic_year
        self.start_year = int(cfg.academic_year[:4])
        self.rules = default_rule_values()
        self.programmes = {p["canonical_code"]: p for p in phase1.PROGRAMMES}
        self.institutions: list[InstitutionRow] = []
        self.inst: dict[str, InstitutionRow] = {}
        self.ips: dict[IPKey, IPRow] = {}
        self.students: list[StudentRow] = []
        self.records: list[RecordRow] = []
        self.registry: dict[str, RegistryRow] = {}
        self.labels: list[LabelRow] = []
        self.persons: dict[int, Person] = {}
        self.used: Counter = Counter()
        self.apaar_taken: set[str] = {phase1.DEMO_STUDENT_APAAR_ID}
        self.phones_taken: set[str] = set()
        self.email_seq: Counter = Counter()
        self.college_seq: Counter = Counter()
        self.reserved: set[int] = set()  # record keys already used by a scenario
        self.records_by_student: dict[int, list[int]] = {}
        self.overcap_ips: set[IPKey] = set()

    # ------------------------------------------------------------------ helpers
    def level(self, prog_code: str) -> ProgrammeLevel:
        return self.programmes[prog_code]["level"]

    def window(self, year: str) -> tuple[date, date]:
        w = self.rules["academic_year.admission_window"].get(year)
        if w:
            return date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
        y = int(year[:4])
        return date(y, 7, 1), date(y, 9, 30)

    def cap(self, ip: IPRow, key: LedgerKey) -> int:
        nk = ip_natural_key(*ip.key)
        return compute_cap(key, ip.approved_intake, ip.shift, nk, self.rules)

    def has_space(self, ip_key: IPKey, key: LedgerKey, n: int = 1) -> bool:
        return self.used[(ip_key, key)] + n <= self.cap(self.ips[ip_key], key)

    def new_apaar(self) -> str:
        while True:
            cand = f"99{self.rng.randrange(10**10):010d}"
            if cand in self.apaar_taken or _looks_fake(cand):
                continue
            self.apaar_taken.add(cand)
            return cand

    def new_phone(self) -> str:
        while True:
            cand = f"9999{self.rng.randrange(10**6):06d}"
            if cand not in self.phones_taken:
                self.phones_taken.add(cand)
                return cand

    def new_email(self, tokens: list[Token]) -> str:
        latin = [t for t, _ in tokens if len(t.rstrip(".")) > 1]
        base = ".".join(
            "".join(c for c in t.lower() if c.isalnum()) for t in latin[:1] + latin[-1:]
        )
        self.email_seq[base] += 1
        return f"{base}{self.email_seq[base]}@mail.example"

    def new_address(self, state: str) -> str:
        info = STATES[state]
        city = self.rng.choice(info["cities"])
        pin = f"{info['pin']}{self.rng.randrange(10**5):05d}"
        return f"{self.rng.randint(1, 450)}, {self.rng.choice(LOCALITIES)}, {city}, {state} - {pin}"

    def college_id(self, inst_code: str, year: str) -> str:
        self.college_seq[inst_code] += 1
        return f"{inst_code}/{year[:4]}/{self.college_seq[inst_code]:05d}"

    def admission_offset(self, inst_code: str, span: int) -> int:
        if self.inst[inst_code].late_burst and self.rng.random() < 0.78:
            return self.rng.randint(span - 4, span)
        return min(span, int(self.rng.triangular(0, span, span * 0.35)))

    def admission_date(self, ip_key: IPKey) -> date:
        start, end = self.window(ip_key[2])
        return start + timedelta(days=self.admission_offset(ip_key[0], (end - start).days))

    def random_dob(self, admission_year: int, ages: tuple[int, int]) -> date:
        age = self.rng.randint(*ages)
        return date(admission_year - age, 1, 1) + timedelta(days=self.rng.randint(0, 364))

    def ensure_ip(self, inst_code: str, prog_code: str, year: str, shift: str) -> IPKey:
        """Prior-year rows exist only to host history records; declared_enrolment is NULL."""
        key = (inst_code, prog_code, year, shift)
        if key not in self.ips:
            current = self.ips.get((inst_code, prog_code, self.year, shift))
            intake = current.approved_intake if current else 60
            self.ips[key] = IPRow(key, intake, None, self.inst[inst_code].is_demo)
        return key

    # ------------------------------------------------------------------ people
    def make_tokens(self, region: str, gender: str) -> list[Token]:
        pool = NAMES[region]
        first = self.rng.choice(pool[gender])
        if region == "SOUTH_TN" and self.rng.random() < 0.4:
            return [(self.rng.choice(string.ascii_uppercase) + ".", None), first]
        if region == "SIKH":
            if self.rng.random() < 0.6:
                surname = ("Singh", None) if gender == "M" else ("Kaur", None)
            else:
                surname = self.rng.choice(pool["S"][2:])
        else:
            surname = self.rng.choice(pool["S"])
        middle: list[Token] = []
        if region == "WEST" and self.rng.random() < 0.35:
            middle = [self.rng.choice(pool["M"])]
        elif region == "NORTH" and gender == "M" and self.rng.random() < 0.12:
            middle = [("Kumar", "कुमार")]
        if middle and middle[0][0] == first[0]:
            middle = []
        return [first, *middle, surname]

    def make_person(
        self,
        state: str,
        gender: str | None,
        dob: date,
        tokens: list[Token] | None = None,
        apaar_id: str | None = None,
        level: ProgrammeLevel = ProgrammeLevel.UG,
    ) -> Person:
        home = state if self.rng.random() < 0.85 else self.rng.choice(sorted(STATES))
        region = _weighted(self.rng, list(STATES[home]["regions"].items()))
        if gender is None:
            gender = "F" if self.rng.random() < FEMALE_SHARE[level] else "M"
        tokens = tokens or self.make_tokens(region, gender)
        key = len(self.students) + 1
        person = Person(key, apaar_id or self.new_apaar(), tokens, dob, gender, region, home)
        self.persons[key] = person
        self.students.append(StudentRow(key, person.apaar_id, person.latin, dob, gender))
        self.registry[person.apaar_id] = RegistryRow(person.apaar_id, person.latin, dob, gender)
        return person

    def add_record(
        self,
        student_key: int,
        ip_key: IPKey,
        category: Cat,
        entry_type: EntryType,
        admission_date: date,
        *,
        status: RecordStatus = RecordStatus.ACTIVE,
        end_date: date | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        nationality: str = "Indian",
        **overrides: Any,
    ) -> RecordRow:
        person = self.persons.get(student_key)
        inst = self.inst[ip_key[0]]
        bucket = ledger_key_for(category, entry_type)
        rec = RecordRow(
            key=len(self.records) + 1,
            student_key=student_key,
            ip_key=ip_key,
            student_college_id=self.college_id(ip_key[0], ip_key[2]),
            admission_date=admission_date,
            end_date=end_date,
            category=category,
            entry_type=entry_type,
            mode=StudyMode.PART_TIME if ip_key[3] == Shift.PART_TIME else StudyMode.FULL_TIME,
            ledger_key=bucket,
            nationality=nationality,
            is_nri=category == Cat.NRI,
            nri_sponsor=None,
            is_oci=category == Cat.OCI,
            oci_number=None,
            is_foreign_national=category == Cat.FN,
            status=status,
            phone=phone or self.new_phone(),
            email=email or self.new_email(person.tokens if person else [("student", None)]),
            address=address or self.new_address(person.home_state if person else inst.state),
        )
        if category == Cat.NRI:
            rel = self.rng.choice(NRI_RELATIONS)
            rec.nri_sponsor = (
                f"{person.tokens[-1][0] if person else 'Sponsor'} ({rel}), "
                f"{self.rng.choice(NRI_COUNTRIES)}"
            )
        if category == Cat.OCI:
            rec.oci_number = f"A{self.rng.randrange(10**7):07d}"
        if category == Cat.FN:  # FR-12 passport note; derived from the key (no RNG draw)
            rec.id_document_note = f"Passport (synthetic) X{rec.key:08d}"
        for k, v in overrides.items():
            setattr(rec, k, v)
        self.records.append(rec)
        self.records_by_student.setdefault(student_key, []).append(rec.key)
        self.used[(ip_key, bucket)] += 1
        return rec

    # ------------------------------------------------------------------ structure
    def build_institutions(self) -> None:
        for data in phase1.INSTITUTIONS:
            row = InstitutionRow(
                code=data["code"],
                name=data["name"],
                state=data["state"],
                district=data["district"],
                type=data["type"],
                kind=DEMO_KINDS[data["code"]],
                is_demo=True,
            )
            self.institutions.append(row)
        n_syn = self.cfg.n_institutions - len(phase1.INSTITUTIONS)
        states = sorted(STATES)
        state_cycle = []
        while len(state_cycle) < n_syn:
            block = states.copy()
            self.rng.shuffle(block)
            state_cycle.extend(block)
        kinds: list[str] = []
        for kind, w in KIND_WEIGHTS:
            kinds.extend([kind] * round(w * n_syn))
        while len(kinds) < n_syn:
            kinds.append("ENGINEERING")
        kinds = kinds[:n_syn]
        self.rng.shuffle(kinds)
        for i in range(n_syn):
            state, kind = state_cycle[i], kinds[i]
            city = self.rng.choice(STATES[state]["cities"])
            ownership = "Government" if self.rng.random() < 0.25 else "Private"
            code = f"SYN-INST-{i + 1:03d}"
            self.institutions.append(
                InstitutionRow(
                    code=code,
                    name=f"Synthetic {KIND_LABELS[kind]} {i + 1:02d}, {city}",
                    state=state,
                    district=city,
                    type=f"{kind.title()} ({ownership}, synthetic)",
                    kind=kind,
                    is_demo=False,
                )
            )
        self.inst = {i.code: i for i in self.institutions}

    def build_ips(self) -> None:
        for inst_code, prog_code, shift, intake, declared in phase1.INSTITUTION_PROGRAMMES:
            key = (inst_code, prog_code, phase1.ACADEMIC_YEAR, shift.value)
            if phase1.ACADEMIC_YEAR == self.year:
                self.ips[key] = IPRow(key, intake, declared, True)
        for inst in self.institutions:
            if inst.is_demo:
                continue
            core, optional, (lo, hi) = PROGRAMME_POOLS[inst.kind]
            k = self.rng.randint(lo, min(hi, len(core) + len(optional)))
            progs = core + self.rng.sample(optional, k - len(core))
            for prog in progs:
                lvl = self.level(prog)
                intake = self.rng.choices(INTAKES, INTAKE_WEIGHTS)[0]
                if prog == "PG-MTECH-CSE":
                    intake = self.rng.choices([60, 120], [0.8, 0.2])[0]
                self._add_ip(inst.code, prog, Shift.REGULAR, intake)
                if lvl == ProgrammeLevel.DIPLOMA and self.rng.random() < 0.30:
                    self._add_ip(inst.code, prog, Shift.SECOND, self.rng.choice([60, 120]))
                elif lvl == ProgrammeLevel.UG and self.rng.random() < 0.08:
                    self._add_ip(inst.code, prog, Shift.SECOND, 60)
                elif prog == "PG-MTECH-CSE" and self.rng.random() < 0.30:
                    self._add_ip(inst.code, prog, Shift.PART_TIME, 60)

    def _add_ip(self, inst_code: str, prog: str, shift: Shift, intake: int) -> None:
        key = (inst_code, prog, self.year, shift.value)
        self.ips[key] = IPRow(key, intake, None, False)

    def current_ips(self, *, demo: bool | None = None) -> list[IPRow]:
        return [
            ip
            for ip in self.ips.values()
            if ip.year == self.year and (demo is None or ip.is_demo == demo)
        ]

    def choose_special(self) -> None:
        syn = [i for i in self.institutions if not i.is_demo]
        n_head = round(self.cfg.headcount_only_share * len(self.institutions))
        n_head = max(1, min(n_head, len(syn) - 1))
        for inst in self.rng.sample(syn, n_head):
            inst.headcount_only = True
        rest = [i for i in syn if not i.headcount_only]
        for inst in self.rng.sample(rest, min(len(rest), self.cfg.scaled(2))):
            inst.late_burst = True
        # Scale synthetic intakes so the national total lands near target_students.
        expected_demo = sum(ip.declared_enrolment or 0 for ip in self.current_ips(demo=True))
        expected_syn = sum(
            ip.approved_intake * 0.93 * (0.45 if self.inst[ip.inst_code].headcount_only else 0.96)
            for ip in self.current_ips(demo=False)
        )
        factor = (self.cfg.target_students - expected_demo) / max(expected_syn, 1)
        if abs(factor - 1) > 0.07:
            for ip in self.current_ips(demo=False):
                ip.approved_intake = max(30, round(ip.approved_intake * factor / 30) * 30)
        # NRI caps for ~15% of regular synthetic programme rows (others keep the default 0).
        nri_caps = self.rules["bucket.NRI"]["by_institution_programme"]
        for ip in self.current_ips(demo=False):
            if ip.shift == Shift.REGULAR and self.rng.random() < 0.15:
                nri_caps[ip_natural_key(*ip.key)] = max(2, round(ip.approved_intake * 0.05))

    # ------------------------------------------------------------------ base population
    def populate(self) -> None:
        clean = [
            ip
            for ip in self.current_ips(demo=False)
            if not self.inst[ip.inst_code].headcount_only
            and not self.inst[ip.inst_code].late_burst
            and ip.shift == Shift.REGULAR
        ]
        nri_caps = self.rules["bucket.NRI"]["by_institution_programme"]
        overcap: dict[IPKey, tuple[LedgerKey, int]] = {}
        picks = self.rng.sample(clean, min(len(clean), 3))
        ug = [ip for ip in clean if self.level(ip.prog_code) == ProgrammeLevel.UG]
        if ug:
            lateral_ip = self.rng.choice(ug)
            overcap[lateral_ip.key] = (LedgerKey.LATERAL, 2)
            picks = [p for p in picks if p.key != lateral_ip.key]
        if picks:
            overcap[picks[0].key] = (LedgerKey.APPROVED, 5)
        no_nri = [p for p in picks[1:] if ip_natural_key(*p.key) not in nri_caps]
        if no_nri:
            overcap[no_nri[0].key] = (LedgerKey.NRI, 2)
        self.overcap_ips = set(overcap)

        demo_key = DEMO_STUDENT["ip"]
        for ip in self.current_ips():
            slots = self._bucket_slots(ip, overcap.get(ip.key))
            inst = self.inst[ip.inst_code]
            if ip.is_demo or ip.key in overcap:
                coverage = 1.0
            elif inst.headcount_only:
                coverage = self.rng.uniform(0.30, 0.60)
            else:
                coverage = 1.0 if self.rng.random() < 0.35 else self.rng.uniform(0.90, 0.995)
            self.rng.shuffle(slots)
            kept = slots[: round(len(slots) * coverage)]
            if ip.key == demo_key and LedgerKey.APPROVED in kept:
                kept.remove(LedgerKey.APPROVED)
                self._add_demo_student()
            for bucket in kept:
                self._base_record(ip, bucket)

    def _bucket_slots(self, ip: IPRow, overcap: tuple[LedgerKey, int] | None) -> list[LedgerKey]:
        lvl = self.level(ip.prog_code)
        regular = ip.shift == Shift.REGULAR
        rng = self.rng
        lat = sup = tfw = nri = 0
        if lvl == ProgrammeLevel.UG and regular:
            lat = min(
                self.cap(ip, LedgerKey.LATERAL), round(ip.approved_intake * rng.uniform(0.02, 0.09))
            )
        cap_sup = self.cap(ip, LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC)
        if cap_sup and rng.random() < 0.45:
            sup = rng.randint(1, min(4, cap_sup))
        cap_tfw = self.cap(ip, LedgerKey.SUPERNUMERARY_TFW)
        if cap_tfw and lvl in (ProgrammeLevel.UG, ProgrammeLevel.DIPLOMA) and rng.random() < 0.5:
            tfw = rng.randint(1, cap_tfw)
        cap_nri = self.cap(ip, LedgerKey.NRI)
        if cap_nri:
            nri = rng.randint(1, cap_nri)
        if ip.declared_enrolment is not None:  # demo rows: declared headcount is fixed
            approved = max(
                0, min(ip.approved_intake, ip.declared_enrolment - lat - sup - tfw - nri)
            )
        else:
            approved = round(ip.approved_intake * min(1.0, rng.uniform(0.70, 1.06)))
        if overcap:
            bucket, excess = overcap
            if bucket == LedgerKey.APPROVED:
                approved = ip.approved_intake + excess
            elif bucket == LedgerKey.LATERAL:
                lat = self.cap(ip, LedgerKey.LATERAL) + excess
            elif bucket == LedgerKey.NRI:
                nri = cap_nri + excess
        slots = (
            [LedgerKey.APPROVED] * approved
            + [LedgerKey.LATERAL] * lat
            + [LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC] * sup
            + [LedgerKey.SUPERNUMERARY_TFW] * tfw
            + [LedgerKey.NRI] * nri
        )
        if ip.declared_enrolment is None:
            ip.declared_enrolment = len(slots)
        return slots

    def _add_demo_student(self) -> None:
        d = DEMO_STUDENT
        key = len(self.students) + 1
        state = self.inst[d["ip"][0]].state
        person = Person(key, d["apaar_id"], d["tokens"], d["dob"], d["gender"], "SOUTH_KA", state)
        self.persons[key] = person
        self.students.append(StudentRow(key, person.apaar_id, person.latin, person.dob, "F"))
        self.registry[person.apaar_id] = RegistryRow(person.apaar_id, person.latin, person.dob, "F")
        rec = self.add_record(key, d["ip"], Cat.GENERAL, EntryType.REGULAR, d["admission_date"])
        self.reserved.add(rec.key)

    def _base_record(self, ip: IPRow, bucket: LedgerKey) -> RecordRow:
        rng = self.rng
        lvl = self.level(ip.prog_code)
        inst = self.inst[ip.inst_code]
        entry = EntryType.LATERAL if bucket == LedgerKey.LATERAL else EntryType.REGULAR
        if bucket in (LedgerKey.APPROVED, LedgerKey.LATERAL):
            category = _weighted(rng, APPROVED_CATEGORY_MIX)
        elif bucket == LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC:
            category = _weighted(rng, SUPERNUMERARY_MIX)
        elif bucket == LedgerKey.SUPERNUMERARY_TFW:
            category = Cat.TFW
        else:
            category = Cat.NRI
        adm = self.admission_date(ip.key)
        dob = self.random_dob(
            adm.year, LATERAL_AGE if entry == EntryType.LATERAL else AGE_RANGE[lvl]
        )
        nationality = "Indian"
        tokens = None
        gender = None
        if category == Cat.FN:
            first, last, gender, nationality = rng.choice(FOREIGN_NATIONALS)
            tokens = [(first, None), (last, None)]
        elif category == Cat.OCI:
            nationality = rng.choice(OCI_COUNTRIES)
        elif category == Cat.PIO:
            nationality = rng.choice(PIO_COUNTRIES)
        person = self.make_person(inst.state, gender, dob, tokens, level=lvl)
        status, end = RecordStatus.ACTIVE, None
        if rng.random() < 0.012:
            status = RecordStatus.WITHDRAWN
            end = adm + timedelta(days=rng.randint(10, 150))
        return self.add_record(
            person.student_key,
            ip.key,
            category,
            entry,
            adm,
            status=status,
            end_date=end,
            nationality=nationality,
        )

    # ------------------------------------------------------------------ scenario helpers
    def candidates(self, *, bucket: LedgerKey = LedgerKey.APPROVED) -> list[RecordRow]:
        """Plain current-year records usable as scenario material."""
        out = []
        for r in self.records:
            if (
                r.key in self.reserved
                or r.ip_key[2] != self.year
                or r.status != RecordStatus.ACTIVE
                or r.ledger_key != bucket
                or r.entry_type != EntryType.REGULAR
                or self.inst[r.inst_code].is_demo
                or len(self.records_by_student[r.student_key]) != 1
            ):
                continue
            out.append(r)
        return out

    def ips_with_space(
        self,
        bucket: LedgerKey,
        *,
        exclude_inst: set[str] = frozenset(),
        level: ProgrammeLevel | None = None,
        prog_code: str | None = None,
        regular_only: bool = False,
    ) -> list[IPRow]:
        return [
            ip
            for ip in self.current_ips(demo=False)
            if ip.inst_code not in exclude_inst
            and ip.key not in self.overcap_ips
            and (level is None or self.level(ip.prog_code) == level)
            and (prog_code is None or ip.prog_code == prog_code)
            and (not regular_only or ip.shift == Shift.REGULAR)
            and self.has_space(ip.key, bucket)
        ]

    def pick_ip(self, bucket: LedgerKey, **kw) -> IPRow | None:
        options = self.ips_with_space(bucket, **kw)
        return self.rng.choice(options) if options else None

    def label(self, scenario: str, group: str, rec: RecordRow | None = None, **kw) -> None:
        if rec is not None:
            kw.setdefault("record_key", rec.key)
            kw.setdefault("student_key", rec.student_key)
            kw.setdefault("inst_code", rec.inst_code)
            kw.setdefault("ip_key", rec.ip_key)
        self.labels.append(LabelRow(scenario, group, **kw))

    # ------------------------------------------------------------------ scenarios
    def lateral_priors(self) -> None:
        """Genuine lateral entrants get a completed diploma record (so they pass
        TIMELINE_LATERAL_NO_PRIOR)."""
        diploma_ips = [
            ip for ip in self.current_ips() if self.level(ip.prog_code) == ProgrammeLevel.DIPLOMA
        ]
        if not diploma_ips:
            return
        prior_year = f"{self.start_year - 3}-{str(self.start_year - 2)[-2:]}"
        start, _ = self.window(prior_year)
        for rec in [r for r in self.records if r.entry_type == EntryType.LATERAL]:
            src = self.rng.choice(diploma_ips)
            key = self.ensure_ip(src.inst_code, src.prog_code, prior_year, src.shift)
            self.add_record(
                rec.student_key,
                key,
                rec.category if rec.category in _APPROVED_CATS else Cat.GENERAL,
                EntryType.REGULAR,
                start + timedelta(days=self.rng.randint(0, 80)),
                status=RecordStatus.GRADUATED,
                end_date=date(self.start_year, 5, 15) + timedelta(days=self.rng.randint(0, 45)),
                phone=rec.phone,
                email=rec.email,
                address=rec.address,
            )

    def transfers(self) -> None:
        start, end = self.window(self.year)
        made = 0
        for _ in range(self.cfg.scaled(30) * 3):
            if made >= self.cfg.scaled(30):
                break
            a = self.pick_ip(LedgerKey.APPROVED, regular_only=True)
            if a is None:
                return
            lvl = self.level(a.prog_code)
            b = self.pick_ip(
                LedgerKey.APPROVED, exclude_inst={a.inst_code}, prog_code=a.prog_code
            ) or self.pick_ip(LedgerKey.APPROVED, exclude_inst={a.inst_code}, level=lvl)
            if b is None:
                continue
            adm_a = start + timedelta(days=self.rng.randint(0, 25))
            end_a = adm_a + timedelta(days=self.rng.randint(7, 25))
            adm_b = min(end, end_a + timedelta(days=self.rng.randint(1, 15)))
            person = self.make_person(
                self.inst[b.inst_code].state,
                None,
                self.random_dob(adm_a.year, AGE_RANGE[lvl]),
                level=lvl,
            )
            cat = _weighted(self.rng, APPROVED_CATEGORY_MIX)
            ra = self.add_record(
                person.student_key,
                a.key,
                cat,
                EntryType.REGULAR,
                adm_a,
                status=RecordStatus.WITHDRAWN,
                end_date=end_a,
            )
            rb = self.add_record(
                person.student_key,
                b.key,
                cat,
                EntryType.REGULAR,
                adm_b,
                phone=ra.phone,
                email=ra.email,
                address=ra.address,
            )
            made += 1
            group = f"TRF-{made:04d}"
            gap = (adm_b - end_a).days
            self.reserved.update({ra.key, rb.key})
            self.label(TRANSFER, group, ra, role="FROM", detail={"gap_days": gap})
            self.label(TRANSFER, group, rb, role="TO", detail={"gap_days": gap})

    def duplicates(self) -> None:
        start, end = self.window(self.year)
        span = (end - start).days
        pool = self.candidates()
        self.rng.shuffle(pool)
        n = min(self.cfg.scaled(300), len(pool))
        made = 0
        for orig in pool:
            if made >= n:
                break
            if orig.key in self.reserved:
                continue
            person = self.persons[orig.student_key]
            lvl = self.level(orig.ip_key[1])
            mode = self.rng.choices(["SAME", "MISSING", "WRONG"], [0.20, 0.28, 0.52])[0]
            copies = 2 if self.rng.random() < 0.10 else 1
            targets: list[IPRow] = []
            exclude = {orig.inst_code}
            for _ in range(copies):
                ip = None
                if self.rng.random() < 0.5:
                    ip = self.pick_ip(
                        LedgerKey.APPROVED, exclude_inst=exclude, prog_code=orig.ip_key[1]
                    )
                ip = ip or self.pick_ip(LedgerKey.APPROVED, exclude_inst=exclude, level=lvl)
                if ip is None:
                    break
                targets.append(ip)
                exclude.add(ip.inst_code)
            if not targets:
                continue
            made += 1
            group = f"DUP-{made:04d}"
            self.reserved.add(orig.key)
            self.label(DUPLICATE, group, orig, role="ORIGINAL", detail={"apaar_mode": mode})
            for ip in targets:
                types = self._duplicate_types(mode, person)
                if mode == "SAME":
                    student_key = orig.student_key
                else:
                    name, dob = C.apply_name_dob_corruptions(
                        person.tokens, person.dob, types, self.rng
                    )
                    apaar = None
                    if mode == "WRONG":
                        apaar = C.wrong_apaar(person.apaar_id, self.rng)
                        while apaar in self.apaar_taken:
                            apaar = C.wrong_apaar(person.apaar_id, self.rng)
                        self.apaar_taken.add(apaar)
                    student_key = len(self.students) + 1
                    self.students.append(StudentRow(student_key, apaar, name, dob, person.gender))
                    self.persons[student_key] = Person(
                        student_key,
                        apaar or "",
                        person.tokens,
                        dob,
                        person.gender,
                        person.region,
                        person.home_state,
                    )
                copy = self.add_record(
                    student_key,
                    ip.key,
                    orig.category,
                    EntryType.REGULAR,
                    start + timedelta(days=self.rng.randint(0, span)),
                    phone=orig.phone if self.rng.random() < 0.35 else None,
                    email=orig.email if self.rng.random() < 0.25 else None,
                    address=orig.address if self.rng.random() < 0.50 else None,
                    nationality=orig.nationality,
                )
                self.reserved.add(copy.key)
                self.label(
                    DUPLICATE,
                    group,
                    copy,
                    role="COPY",
                    corruption_types=types,
                    detail={"apaar_mode": mode, "split": C.split_for(types)},
                )

    def _duplicate_types(self, mode: str, person: Person) -> list[str]:
        if mode == "SAME":
            return []
        apaar_type = C.APAAR_MISSING if mode == "MISSING" else C.APAAR_WRONG
        options = [t for t in C.NAME_DOB_TYPES if t != C.DOB_DAY_MONTH_SWAP]
        if C.can_swap_day_month(person.dob):
            options.append(C.DOB_DAY_MONTH_SWAP)
        if len(person.tokens) < 2 or any(t.endswith(".") for t, _ in person.tokens):
            options = [t for t in options if t != C.INITIALS]
        if len(person.tokens) < 2:
            options = [t for t in options if t != C.SWAPPED_ORDER]
        r = self.rng.random()
        if r < 0.10:
            chosen: list[str] = []  # APAAR corruption only
        elif r < 0.60:
            chosen = [self.rng.choice(options)]
        else:
            chosen = self.rng.sample(options, min(len(options), self.rng.choice([2, 2, 3])))
        return sorted(chosen, key=C.CORRUPTION_TYPES.index) + [apaar_type]

    def timelines(self) -> None:
        n = self.cfg.scaled(8, minimum=2)
        prior = f"{self.start_year - 1}-{str(self.start_year)[-2:]}"
        p_start, _ = self.window(prior)
        start, end = self.window(self.year)
        idx = Counter()

        def group(kind: str) -> str:
            idx[kind] += 1
            return f"TL-{kind}-{idx[kind]:03d}"

        for _ in range(n):  # OVERLAP: ongoing UG (prior year) + full-time PG elsewhere
            ug = self.pick_ip(LedgerKey.APPROVED, level=ProgrammeLevel.UG, regular_only=True)
            pg = None
            if ug:
                for lvl in (ProgrammeLevel.PGDM, ProgrammeLevel.MCA, ProgrammeLevel.PG):
                    pg = self.pick_ip(LedgerKey.APPROVED, exclude_inst={ug.inst_code}, level=lvl)
                    if pg and pg.shift != Shift.PART_TIME:
                        break
                    pg = None
            if not (ug and pg):
                break
            key_prior = self.ensure_ip(ug.inst_code, ug.prog_code, prior, ug.shift)
            p = self.make_person(
                self.inst[pg.inst_code].state, None, self.random_dob(self.start_year, (20, 22))
            )
            r1 = self.add_record(
                p.student_key,
                key_prior,
                Cat.GENERAL,
                EntryType.REGULAR,
                p_start + timedelta(days=self.rng.randint(20, 70)),
            )
            r2 = self.add_record(
                p.student_key,
                pg.key,
                Cat.GENERAL,
                EntryType.REGULAR,
                self.admission_date(pg.key),
                phone=r1.phone,
                email=r1.email,
            )
            g = group("OVERLAP")
            for r in (r1, r2):
                self.reserved.add(r.key)
                self.label(TIMELINE, g, r, corruption_types=["TIMELINE_OVERLAP"])

        diploma_ips = [
            ip for ip in self.current_ips() if self.level(ip.prog_code) == ProgrammeLevel.DIPLOMA
        ]
        for _ in range(n):  # TOO_SHORT: 3-year diploma "graduated" after one year, then lateral
            ug = self.pick_ip(LedgerKey.LATERAL, level=ProgrammeLevel.UG, regular_only=True)
            if not (ug and diploma_ips):
                break
            src = self.rng.choice(diploma_ips)
            key_prior = self.ensure_ip(src.inst_code, src.prog_code, prior, src.shift)
            p = self.make_person(
                self.inst[ug.inst_code].state, None, self.random_dob(self.start_year, LATERAL_AGE)
            )
            r1 = self.add_record(
                p.student_key,
                key_prior,
                Cat.GENERAL,
                EntryType.REGULAR,
                p_start + timedelta(days=self.rng.randint(0, 60)),
                status=RecordStatus.GRADUATED,
                end_date=date(self.start_year, 5, 20),
            )
            r2 = self.add_record(
                p.student_key,
                ug.key,
                Cat.GENERAL,
                EntryType.LATERAL,
                self.admission_date(ug.key),
                phone=r1.phone,
                email=r1.email,
            )
            g = group("TOO_SHORT")
            for r in (r1, r2):
                self.reserved.add(r.key)
                self.label(TIMELINE, g, r, corruption_types=["TIMELINE_TOO_SHORT"])

        for _ in range(n):  # LATERAL_NO_PRIOR
            ug = self.pick_ip(LedgerKey.LATERAL, level=ProgrammeLevel.UG, regular_only=True)
            if not ug:
                break
            p = self.make_person(
                self.inst[ug.inst_code].state, None, self.random_dob(self.start_year, LATERAL_AGE)
            )
            r = self.add_record(
                p.student_key, ug.key, Cat.GENERAL, EntryType.LATERAL, self.admission_date(ug.key)
            )
            self.reserved.add(r.key)
            self.label(
                TIMELINE,
                group("LATERAL_NO_PRIOR"),
                r,
                corruption_types=["TIMELINE_LATERAL_NO_PRIOR"],
            )

        for i in range(n):  # INVALID_DATE: admission in the future, or end before admission
            ip = self.pick_ip(LedgerKey.APPROVED)
            if not ip:
                break
            lvl = self.level(ip.prog_code)
            p = self.make_person(
                self.inst[ip.inst_code].state,
                None,
                self.random_dob(self.start_year, AGE_RANGE[lvl]),
                level=lvl,
            )
            if i % 2 == 0:
                r = self.add_record(
                    p.student_key,
                    ip.key,
                    Cat.GENERAL,
                    EntryType.REGULAR,
                    date(self.start_year + 5, 1, 15),
                )
                kind = "future_admission"
            else:
                adm = start + timedelta(days=self.rng.randint(30, (end - start).days))
                r = self.add_record(
                    p.student_key,
                    ip.key,
                    Cat.GENERAL,
                    EntryType.REGULAR,
                    adm,
                    status=RecordStatus.WITHDRAWN,
                    end_date=adm - timedelta(days=self.rng.randint(5, 40)),
                )
                kind = "end_before_admission"
            self.reserved.add(r.key)
            self.label(
                TIMELINE,
                group("INVALID_DATE"),
                r,
                corruption_types=["TIMELINE_INVALID_DATE"],
                detail={"kind": kind},
            )

    def rings(self) -> None:
        pool = self.candidates()
        by_inst: dict[str, list[RecordRow]] = {}
        for r in pool:
            by_inst.setdefault(r.inst_code, []).append(r)
        insts = sorted(by_inst)
        for i in range(self.cfg.scaled(2)):
            size = self.rng.randint(25, 60)
            chosen_insts = self.rng.sample(insts, min(len(insts), self.rng.randint(2, 4)))
            members = [
                r for code in chosen_insts for r in by_inst[code] if r.key not in self.reserved
            ]
            if len(members) < size:
                continue
            members = self.rng.sample(members, size)
            phone = self.new_phone()
            group = f"RING-{i + 1:02d}"
            for r in members:
                r.phone = phone
                self.reserved.add(r.key)
                self.label(
                    RING,
                    group,
                    r,
                    role="MEMBER",
                    detail={"shared": "phone", "size": size, "institutions": len(chosen_insts)},
                )

    def natural_shared_phones(self) -> None:
        """Siblings sharing a parent's phone: below the ring threshold (hard negatives)."""
        pool = [r for r in self.candidates()]
        self.rng.shuffle(pool)
        n = min(self.cfg.scaled(60), len(pool) // 2)
        for i in range(n):
            a, b = pool[2 * i], pool[2 * i + 1]
            b.phone = a.phone
            group = f"SIB-{i + 1:04d}"
            for r in (a, b):
                self.reserved.add(r.key)
                self.label(NATURAL_SHARED_PHONE, group, r, role="MEMBER")

    def rule_faults(self) -> None:
        n = self.cfg.scaled(5)
        start, end = self.window(self.year)
        pool = self.candidates()
        self.rng.shuffle(pool)
        it = iter(pool)
        count = Counter()

        def fault(kind: str, rec: RecordRow, **detail) -> None:
            count[kind] += 1
            self.reserved.add(rec.key)
            self.label(
                RULE_FAULT,
                f"RF-{kind}-{count[kind]:03d}",
                rec,
                corruption_types=[kind],
                detail=detail,
            )

        students = {s.key: s for s in self.students}
        suspicious = [d * 12 for d in "0123456789"] + [
            "".join(str((s + k) % 10) for k in range(12)) for s in range(10)
        ]
        self.rng.shuffle(suspicious)
        for i in range(n):
            rec = next(it, None)
            if rec is None:
                return
            st = students[rec.student_key]
            bad = st.apaar_id[:11] if i % 2 == 0 else st.apaar_id[:6] + "O" + st.apaar_id[7:]
            if bad in self.apaar_taken:
                continue
            self.apaar_taken.add(bad)
            st.apaar_id = bad
            fault("APAAR_INVALID", rec, submitted=bad)
        for _ in range(n):
            rec = next(it, None)
            if rec is None:
                return
            value = next(s for s in suspicious if s not in self.apaar_taken)
            self.apaar_taken.add(value)
            students[rec.student_key].apaar_id = value
            fault("APAAR_SUSPICIOUS", rec, submitted=value)
        for _ in range(n):  # submitted DOB differs from the registry
            rec = next(it, None)
            if rec is None:
                return
            st = students[rec.student_key]
            st.dob = st.dob - timedelta(days=365)
            fault("APAAR_MISMATCH", rec)
        while count["DUP_EXACT_SAME_INST"] < n:  # same student submitted twice, same programme
            rec = next(it, None)
            if rec is None:
                return
            if not self.has_space(rec.ip_key, LedgerKey.APPROVED):
                continue
            dup = self.add_record(
                rec.student_key,
                rec.ip_key,
                rec.category,
                EntryType.REGULAR,
                rec.admission_date,
                phone=rec.phone,
                email=rec.email,
                address=rec.address,
            )
            self.reserved.add(rec.key)
            fault("DUP_EXACT_SAME_INST", dup, first_record_key=rec.key)
        specs = [
            ("OCI_NO_NUMBER", LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC, Cat.OCI),
            ("FN_INDIAN_NATIONALITY", LedgerKey.SUPERNUMERARY_FN_OCI_PIO_CIWGC, Cat.FN),
            ("NRI_NO_SPONSOR", LedgerKey.NRI, Cat.NRI),
            ("ADMISSION_OUTSIDE_WINDOW", LedgerKey.APPROVED, Cat.GENERAL),
        ]
        for kind, bucket, cat in specs:
            for _ in range(n):
                ip = self.pick_ip(bucket, regular_only=True)
                if ip is None:
                    break
                lvl = self.level(ip.prog_code)
                adm = self.admission_date(ip.key)
                if kind == "ADMISSION_OUTSIDE_WINDOW":
                    adm = end + timedelta(days=self.rng.randint(20, 60))
                p = self.make_person(
                    self.inst[ip.inst_code].state,
                    None,
                    self.random_dob(adm.year, AGE_RANGE[lvl]),
                    level=lvl,
                )
                nationality = {"OCI_NO_NUMBER": "Canada", "FN_INDIAN_NATIONALITY": "Indian"}.get(
                    kind, "Indian"
                )
                rec = self.add_record(
                    p.student_key, ip.key, cat, EntryType.REGULAR, adm, nationality=nationality
                )
                if kind == "OCI_NO_NUMBER":
                    rec.oci_number = None
                if kind == "NRI_NO_SPONSOR":
                    rec.nri_sponsor = None
                fault(kind, rec)

    def registry_variants(self) -> None:
        """Registry spelling/script differs from what the institution submitted (the name
        check must still match; hard negatives for APAAR_MISMATCH)."""
        pool = self.candidates()
        self.rng.shuffle(pool)
        for i, rec in enumerate(pool[: self.cfg.scaled(400)]):
            person = self.persons[rec.student_key]
            name = C.transliteration(person.tokens, self.rng, allow_script=True)
            if name == person.latin:
                continue
            self.registry[person.apaar_id].full_name = name
            kind = "script" if not name.isascii() else "spelling"
            self.reserved.add(rec.key)
            self.label(REGISTRY_VARIANT, f"REG-{i + 1:04d}", rec, detail={"kind": kind})

    def registry_extras(self) -> None:
        """People in the (MOCK) registry who are not enrolled anywhere in this dataset."""
        for _ in range(self.cfg.scaled(400)):
            state = self.rng.choice(sorted(STATES))
            region = _weighted(self.rng, list(STATES[state]["regions"].items()))
            gender = self.rng.choice("MF")
            tokens = self.make_tokens(region, gender)
            apaar = self.new_apaar()
            dob = self.random_dob(self.start_year, (17, 25))
            self.registry[apaar] = RegistryRow(apaar, " ".join(t for t, _ in tokens), dob, gender)

    def institution_labels(self) -> None:
        start, end = self.window(self.year)
        last5 = end - timedelta(days=4)
        for inst in self.institutions:
            ips = [ip for ip in self.current_ips() if ip.inst_code == inst.code]
            recs = [
                r for r in self.records if r.inst_code == inst.code and r.ip_key[2] == self.year
            ]
            if inst.headcount_only:
                declared = sum(ip.declared_enrolment or 0 for ip in ips)
                self.label(
                    HEADCOUNT_ONLY,
                    f"HC-{inst.code}",
                    inst_code=inst.code,
                    detail={
                        "declared": declared,
                        "received": len(recs),
                        "coverage": round(len(recs) / max(declared, 1), 3),
                    },
                )
            if inst.late_burst:
                late = sum(1 for r in recs if last5 <= r.admission_date <= end)
                self.label(
                    LATE_BURST,
                    f"BURST-{inst.code}",
                    inst_code=inst.code,
                    detail={"share_last_5_days": round(late / max(len(recs), 1), 3)},
                )
        for key in sorted(self.overcap_ips):
            ip = self.ips[key]
            for bucket in LedgerKey:
                cap, used = self.cap(ip, bucket), self.used[(key, bucket)]
                if used > cap:
                    self.label(
                        OVER_CAP,
                        f"OC-{ip_natural_key(*key)}",
                        inst_code=key[0],
                        ip_key=key,
                        corruption_types=[bucket.value],
                        detail={
                            "bucket": bucket.value,
                            "cap": cap,
                            "used": used,
                            "excess": used - cap,
                        },
                    )

    # ------------------------------------------------------------------ run
    def run(self) -> Dataset:
        self.build_institutions()
        self.build_ips()
        self.choose_special()
        self.populate()
        self.lateral_priors()
        self.transfers()
        self.duplicates()
        self.timelines()
        self.rings()
        self.natural_shared_phones()
        self.rule_faults()
        self.registry_variants()
        self.registry_extras()
        self.institution_labels()
        return Dataset(
            config=self.cfg,
            institutions=self.institutions,
            ips=list(self.ips.values()),
            students=self.students,
            records=self.records,
            registry=list(self.registry.values()),
            labels=self.labels,
            quota_rules=self.rules,
        )


_APPROVED_CATS = frozenset(c for c, _ in APPROVED_CATEGORY_MIX)


def _looks_fake(apaar_id: str) -> bool:
    """Avoid generating IDs that FR-6 would flag as suspicious (same digit / sequential)."""
    if len(set(apaar_id)) == 1:
        return True
    run = 1
    for a, b in zip(apaar_id, apaar_id[1:], strict=False):
        run = run + 1 if abs(int(b) - int(a)) == 1 else 1
        if run >= 6:
            return True
    return False


def generate(config: GeneratorConfig | None = None) -> Dataset:
    return _Generator(config or GeneratorConfig()).run()
