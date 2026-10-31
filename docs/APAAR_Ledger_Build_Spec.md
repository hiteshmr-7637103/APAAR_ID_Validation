# APAAR Ledger: Prototype Build Spec v2

> Hand this whole file to the build agent. It contains the problem context, the frozen promises we made in our submitted PPT, the scope, stack, requirements, data model, API, ML specs, build order, tests and demo script. Follow it as written. Where something is marked **ASSUMPTION**, **CONFIGURABLE** or **VERIFY**, do not hard-code it as fact.
>
> **v2 change summary:** the submitted PPT is frozen, so this spec was rewritten to match it exactly. Main changes from v1: the Seat Ledger now FLAGS instead of rejecting; ML never auto-links; student-confirmed trust tiers T0 to T3 added; coverage meter added; lateral-entry bucket added; transfer-aware duplicate logic added; cross-institution cases are AICTE-only; an evaluation module added; Hindi, policy simulator, HTML reports and 100k scale descoped.

---

## 0. How to work (instructions to the build agent)

1. Build in the phases in section 15, in order. Each phase ships **backend and its UI together** (vertical slices), not all frontend at the end.
2. At the start of each phase, restate its goal in two lines. At the end: run all tests, run the app, commit (`phaseN: <summary>`), and report what works, what is mocked and what is deferred, before moving on.
3. Everything must run locally with one command: `docker compose up`. Seed data loads automatically.
4. Prefer simple, readable code over clever code. This is a hackathon prototype that must demo reliably.
5. **Never fake results.** If a component is mocked (APAAR registry, authority APIs, SMS/email), label it **MOCK** in code, UI and README.
6. Keep ML models small and CPU-only. Train from the synthetic generator at first start, or ship pre-trained artifacts in `backend/app/ml/artifacts/`.
7. Do not invent legal or policy facts. Quota caps live in a config table (section 7.3) with defaults marked **VERIFY**.
8. **The PPT contract (section 1.2) overrides anything else in this file.** If you find a conflict, follow section 1.2 and note the conflict in `docs/decisions.md`.
9. If a requirement is ambiguous, choose the simplest option consistent with section 1.2, record the choice in `docs/decisions.md`, and continue. Ask the user only if blocked.
10. Write `README.md` with setup, demo accounts, the demo script (section 16), the traceability table (PPT claim -> requirement -> code -> test) and known limitations (section 17).

---

## 1. Context

**Event:** Smart India Hackathon (SIH) 2026. Team **Spitzen Squad** (Team ID P121). Problem Statement **SIH26263**. Theme: Smart Automation. Category: Software. Organisation: AICTE, Regulation Bureau.

**Problem (summary):** AICTE gets student data from approved institutions through different channels. PGDM institutions give detailed student data yearly; most other institutions only give aggregate figures (such as total enrolment) during approval. This makes it hard to keep complete, authentic and verifiable student records. APAAR ID (a 12-digit national academic identity, linked to the Academic Bank of Credits and DigiLocker) should be the unique student identifier. The solution must let institutions securely submit student-level data after admissions close, and enable real-time verification and monitoring.

**Ten key requirements from the problem statement (all must be covered):**
1. Secure platform/API for institutions to upload and update student-level data keyed on APAAR ID.
2. Data fields: Approved Intake, Actual Enrolment, Supernumerary Seats, NRI Category, OCI Status, Foreign National (FN) Status, other relevant student information.
3. Automated validation: duplicate records; invalid/missing APAAR IDs; admissions exceeding approved intake; data inconsistencies; potential duplicate enrolment across institutions.
4. Dashboards for AICTE and for institutions, real time.
5. Secure verification mechanism for authorised stakeholders (AICTE, institutions, employers, other regulators).
6. Verifiable student records linked to the academic identity.
7. Integration/API capability with existing institutional and AICTE systems.
8. Security, privacy, authentication, role-based access control, audit trails, consent mechanisms.
9. Reports for policy-making, accreditation, monitoring, regulation.
10. Print/display APAAR ID alongside the student ID on certificates issued by degree-awarding bodies.

**Expected pipeline from the problem statement:**
`Institution -> Student Data Upload -> APAAR Validation -> Data Verification -> AICTE Central Repository -> Analytics & Monitoring -> Authorized Verification`

**Core design principle (decided):** *Rules decide, ML ranks and suggests, humans confirm.* Deterministic rules handle arithmetic and lookup (seat buckets, invalid ID, exact duplicates, timelines). ML is used only where a rule cannot be written (fuzzy duplicates, unusual institution patterns, shared-identity clusters). **ML never rejects, deletes, links or changes the status of a record on its own.**

### 1.1 One-sentence pitch
We do not just collect student data; we make every enrolment number provable, bucket by bucket and student by student, without exposing anyone's data.

### 1.2 The frozen PPT contract (what we promised judges; the prototype must show each item)

| # | PPT claim (slide) | What the prototype must do | Req IDs |
|---|---|---|---|
| P1 | Institutions submit student-level data keyed on APAAR ID; **registrar approves** before records reach AICTE (S2, S3 step 4) | Maker-checker approval; maker cannot approve own batch | FR-1, FR-17 |
| P2 | AICTE validates, monitors, verifies in real time (S2) | Live dashboards via SSE | FR-22 |
| P3 | Hybrid engine: rules decide, ML ranks, humans confirm (S2, S3) | No ML auto-action anywhere; every case needs a human decision | FR-14, NFR-8 |
| P4 | **Seat Ledger: per-bucket intake tracking (supernumerary, lateral entry, categories), flagged at upload** (S2) | Buckets incl. LATERAL; over-cap is **flagged and held for an AICTE officer**, never silently rejected; dry-run shows it | FR-10, FR-11 |
| P5 | **Student-confirmed enrolment with trust tiers T0 to T3** (S2) | Student confirms or disputes; tier rises; dashboards split by tier | FR-18 to FR-20 |
| P6 | ML duplicate matcher with confidence zones; **transfer-aware (interval check)** (S2, S3) | Calibrated zones; genuine transfers produce no case | FR-14, ML-1 |
| P7 | Impossible-timeline detection (S2) | Deterministic timeline checks | FR-13, section 9.5 |
| P8 | Privacy-preserving hashed duplicate check (S2) | Tokens for exact matching; institutions never see another institution's data | FR-37 |
| P9 | Offline-verifiable signed QR and student access log (S2) | Browser-side Ed25519 verify; access log screen | FR-30 to FR-33 |
| P10 | Consent-based verification: only student-approved fields visible (S2) | Consent flow; fresh signed record per consent | FR-28 to FR-31 |
| P11 | Certificate print: APAAR ID + student ID + signed QR (S2) | Printable certificate view | FR-34 |
| P12 | Reports for policy, accreditation, monitoring (S2) | CSV reports | FR-24 |
| P13 | Dry-run upload with row-level fix report (S4) | Sandbox with suggested fixes | FR-3 |
| P14 | **Coverage meter: declared vs records received** (S4) | Per institution/programme, on both dashboards | FR-21 |
| P15 | Phonetic blocking + LightGBM, calibrated (S3) | As ML-1 | ML-1 |
| P16 | Isolation Forest on peer-normalised features, per-feature explanations (S3) | As ML-2 | ML-2 |
| P17 | Louvain graph rings (S3) | As ML-3 | ML-3 |
| P18 | TF-IDF char n-grams programme mapping (S3) | As ML-4 | ML-4 |
| P19 | **"Multilingual name matching"** (S3) | Transliteration-aware matching (Latin + Devanagari names, variant tables, phonetics); optional LaBSE flag. README must describe exactly what is implemented | ML-1 |
| P20 | Hash-chained audit log, Ed25519 selective-disclosure credentials (S3) | Audit chain with verify endpoint; signed per-consent records | FR-35, FR-30 |
| P21 | Rules-as-data: policy change without code change (S4) | Versioned `quota_rules` and rule config | section 7.3 |
| P22 | Resumable uploads, offline QR, blocking and indexing (S4) | Chunked upload; offline verify; blocked candidate generation | FR-2, ML-1 |
| P23 | **Measured in demo: precision/recall on injected faults, review time saved** (S5) | Evaluation page and script | section 14 |
| P24 | PostgreSQL row-level locks for Seat Ledger (S3) | `SELECT ... FOR UPDATE` | section 7.3 |

---

## 2. Prototype scope

### 2.1 In scope (must build)
Institution portal (login, single-record entry, CSV bulk upload with dry run, submission, maker-checker); rule engine with Seat Ledger; trust tiers with student confirmation; ML triage (duplicates, anomalies, rings); AICTE and institution dashboards with coverage meter; review queue; consent-based verification with signed offline-verifiable QR; student access log; hash-chained audit log; CSV reports; certificate view; synthetic data generator with fraud-injection demo mode; evaluation module.

### 2.2 Mocked (label **MOCK** everywhere)
- **APAAR registry:** no public API. Build `/mock/apaar/{id}` backed by synthetic records (name, DOB, gender) behind an `ApaarClient` interface so a real client can replace it.
- **Authority / ABC / DigiLocker:** interface only, mock implementation.
- **Aadhaar/e-KYC:** not implemented.
- **Email/SMS:** consent requests and enrolment confirmations are written to a `notifications` table shown in the UI.

### 2.3 Out of scope
Real government integrations, payments, native mobile apps, legal DPDP review, production key management, multi-region scale tests.

### 2.4 Descoped to protect the schedule (not in the PPT)
Hindi strings (keep i18n keys with English values only), policy simulator endpoint, HTML report export (CSV only), 100k scale target (target about 20,000 records; configurable up to 50,000), per-API-key rate limiting (keep login rate limiting). Do not build these unless every phase is finished.

---

## 3. Tech stack (fixed)

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11, FastAPI, Pydantic v2 | Sync SQLAlchemy is fine |
| ORM / migrations | SQLAlchemy 2.x, Alembic | |
| Database | PostgreSQL 16 | `SELECT ... FOR UPDATE` for the Seat Ledger; `pg_trgm` for blocking |
| Background work | FastAPI `BackgroundTasks` | No queue needed |
| Auth | OAuth2 password flow, JWT (access + refresh), `passlib[bcrypt]` | RBAC via roles claim |
| ML | scikit-learn (IsolationForest, calibration), LightGBM, NetworkX (`louvain_communities`), RapidFuzz, `jellyfish`, `unidecode` or `indic-transliteration`, pandas, numpy; SHAP optional | CPU only |
| Optional ML | LaBSE via `sentence-transformers` | OFF by default (`ENABLE_LABSE=false`) |
| Crypto | `cryptography` (Ed25519, Fernet), `hmac`/`hashlib` | |
| QR | `qrcode` | |
| Frontend | React 18, TypeScript, Vite, TailwindCSS, React Router, TanStack Query, Recharts | |
| Realtime | Server-Sent Events at `/events/stream` | |
| Packaging | Docker Compose: `db`, `api`, `web` | |
| Tests | pytest, httpx; Vitest for the browser verifier utils | |
| Lint | ruff, black, eslint, prettier | |

**Repo layout:**
```
apaar-ledger/
  docker-compose.yml  .env.example  Makefile  README.md
  backend/
    app/
      main.py
      core/ (config.py, security.py, rbac.py, audit.py, events.py, ratelimit.py)
      db/ (models.py, session.py, seed.py)
      api/ (auth.py, institutions.py, students.py, uploads.py, validation.py, review.py,
            dashboards.py, trust.py, verification.py, consent.py, reports.py,
            certificates.py, audit.py, demo.py, evaluation.py, mock_apaar.py, rules_admin.py)
      services/ (rule_engine.py, seat_ledger.py, timeline.py, transfer.py, trust.py,
                 apaar_client.py, triage.py, privacy_tokens.py, signing.py,
                 alerts.py, coverage.py, names.py)
      ml/ (features.py, dedupe.py, anomaly.py, rings.py, programme_map.py, train.py, artifacts/)
      synth/ (generator.py, corruptions.py)
      eval/ (run_eval.py)
    alembic/  tests/
  frontend/src/ (pages/, components/, api/, hooks/, i18n/, lib/verify.ts)
  scripts/ (verify_offline.py, concurrency_demo.py, perf.py, sample_client.py)
  docs/ (architecture.md, api.md, ml.md, security.md, decisions.md, evaluation.md)
```

---

## 4. Users and roles (RBAC)

| Role | Who | Can do |
|---|---|---|
| `AICTE_ADMIN` | AICTE officer | Everything: all dashboards, rules config, all cases, reports, audit log, demo mode, evaluation |
| `AICTE_REVIEWER` | AICTE analyst | View all data, decide cases, export reports; cannot change rules |
| `INST_MAKER` | Data-entry operator | Create/edit/upload records for own institution; submit for approval |
| `INST_CHECKER` | Registrar | Approve/reject maker batches; resolve own institution's `INSTITUTION`-visible cases |
| `VERIFIER` | Employer / regulator | Request verification; sees only consented fields |
| `STUDENT` | Student | See own enrolments, confirm or dispute them, grant/deny/revoke consent, view access log |

**Rules:** institution users see only their own institution (row-level scoping in every query via one shared helper). A maker cannot approve their own batch. **Cases with `visibility = AICTE_ONLY` are never returned to institution users.** Every sensitive action writes an audit entry. Seed one demo account per role, 3 demo institutions, and one student account linked to a seeded APAAR ID.

---

## 5. Functional requirements

IDs are used in tests and the README traceability table.

### 5.1 Data capture (FR-1 to FR-5)
- **FR-1** Institution users create/update a student record with the fields in 7.1. APAAR ID is the unique person key.
- **FR-2** CSV bulk upload with a downloadable template. Chunked and resumable by `upload_id` + chunk index (store chunks, assemble on finalize). Re-finalizing the same upload is idempotent.
- **FR-3** **Dry run:** uploading a CSV first returns a per-row report (errors, warnings, info, **suggested fix**) and a **projected seat-bucket impact** ("Row 41: would exceed LATERAL by 2") without writing to the repository. Only Submit commits.
- **FR-4** Updates create a new version in `student_record_versions` (append-only). A material update (name, DOB, programme, bucket) resets the trust tier to T1 and re-runs checks.
- **FR-5** Programme names map to canonical AICTE programme codes (ML-4); matches below 0.8 are suggestions the user must confirm.

### 5.2 Deterministic validation (FR-6 to FR-13)
Each outcome has `code`, `severity` (ERROR must be fixed, WARNING needs justification, INFO), `field`, `message_en`, `evidence` (JSON). Messages use i18n keys so Hindi can be added later.
- **FR-6** APAAR ID format: exactly 12 digits, else `APAAR_INVALID`. Also flag obviously fake patterns (all same digit, sequential run) as `APAAR_SUSPICIOUS` (WARNING).
- **FR-7** APAAR exists in the (MOCK) registry, else `APAAR_NOT_FOUND`. Name (fuzzy) and DOB (exact) must match the registry, else WARNING `APAAR_MISMATCH`.
- **FR-8** Exact duplicate: same APAAR ID, institution, programme, year, else `DUP_EXACT_SAME_INST` (ERROR).
- **FR-9** Cross-institution duplicate by APAAR ID with **overlapping enrolment intervals**: AICTE-only case `DUP_EXACT_CROSS`; the institution sees only the neutral INFO message `ENROLMENT_ELSEWHERE_NEUTRAL` ("A possible active enrolment exists elsewhere; please verify this student's details"), with **no institution name or details**.
- **FR-10** **Seat Ledger (flag mode):** every admission is a transaction against its bucket (7.3). If the admission pushes `used` above `cap`, the record is still stored (status `PENDING_APPROVAL`, `over_cap = true`), an AICTE-only case `SEAT_OVER_CAP` is opened with bucket, cap, used and excess, and the institution sees WARNING `SEAT_OVER_CAP` with the same numbers about its own data. **Nothing is silently rejected.** Must be atomic under concurrency.
- **FR-11** AICTE officer decision on `SEAT_OVER_CAP`: **Accept exception** (flag marked reviewed, with note) or **Request correction** (record -> `NEEDS_CORRECTION`, seat released, institution notified). Rejected or corrected-away records release their seat (ledger delta -1).
- **FR-12** Consistency checks: FN requires nationality not Indian and a passport/ID note; OCI requires an OCI number; NRI requires sponsor details; admission date within the academic-year window; DOB plausible for programme level; required fields present; programme exists in the institution's approved list.
- **FR-13** Impossible-timeline checks (9.5).

### 5.3 Trust tiers and student confirmation (FR-18 to FR-20; numbered here for traceability)
- **FR-18** Every record carries `trust_tier`:
  - **T0** submitted by institution (on ingest)
  - **T1** passed rule checks with no ERROR
  - **T2** APAAR ID matched the registry adapter (name fuzzy + exact DOB)
  - **T3** confirmed by the student
  Tier changes are logged in `trust_events` (who, why, when). Tiers only rise by their own conditions; a material edit resets to T1.
- **FR-19** When a record becomes `ACTIVE` (after checker approval), create an `enrolment_confirmations` row and a notification for the student. Student portal: **"Confirm enrolment"** (-> T3) or **"Not me"** (-> case `STUDENT_DISPUTE`, AICTE-only, record flagged, tier unchanged). Confirmation requests expire after 30 days (CONFIGURABLE); expired stays at its tier.
- **FR-20** Dashboards show enrolment **by tier** nationally and per institution (e.g. "480 reported, 310 at T3, 120 at T2, 50 at T1"), plus a trust ratio (T3 share of records).

### 5.4 ML triage (FR-14 to FR-16)
- **FR-14** After rules pass, run triage: blocking, then pairwise scoring against other institutions' records, producing a calibrated `match_probability`. Routing (thresholds CONFIGURABLE): `>= 0.90` open a **high-priority case labelled "likely duplicate"**; `0.60 to 0.90` open a review case; `< 0.60` ignore. **No auto-link. Every case needs a human decision.** Transfer-aware: if the other enrolment ended on or before the new admission date, emit INFO `TRANSFER_DETECTED` and no case.
- **FR-15** Institution anomaly detection per institution and academic year (on submit and via a manual "Recompute" button): anomaly score, rank, top contributing features.
- **FR-16** Shared-identity ring detection: record-to-contact graph (phone, email, address tokens); flag Louvain communities where one identifier is shared by more than `k` distinct APAAR IDs (default k=3) or size exceeds a threshold.

### 5.5 Maker-checker and review (FR-17, FR-17b)
- **FR-17** Pipeline order on Submit: rules -> seat reserve (flag mode) -> trust T1/T2 -> ML triage (background) -> `PENDING_APPROVAL` -> checker decision -> `ACTIVE` (or `REJECTED`, which releases seats). Maker cannot approve own batch.
- **FR-17b** Review queue: cases with evidence and explanation. Actions by type: duplicates (Confirm duplicate / Not a duplicate / Escalate / Request correction), anomaly (Acknowledge / Escalate for audit), ring (Confirm / Dismiss), seat over-cap (FR-11), student dispute (Uphold dispute / Reject dispute). Decisions are stored and become ML labels (`ml_feedback`). "Retrain with feedback" button in demo mode.

### 5.6 Dashboards and reports (FR-21 to FR-25)
- **FR-21** **Coverage meter:** `coverage = records received (non-rejected) / declared_enrolment` per programme and institution, shown as a bar with colour bands (CONFIGURABLE: <50% red, 50-90% amber, >=90% green). The generator makes 25-30% of institutions headcount-only (coverage 30-60%).
- **FR-22** AICTE dashboard: total institutions, students, enrolment vs approved intake (national, by state, by programme), **over-cap flags** (count and excess), coverage distribution, **enrolment by trust tier**, open cases by type, institution risk leaderboard with breakdown, cross-institution duplicate cases, trend over time. Auto-refresh via SSE.
- **FR-23** Institution dashboard: seat utilisation per bucket with caps, coverage meter, trust-tier split, submission status, validation summary, **Data Health Score** (0-100: completeness, validity, timeliness, warning share), pending cases.
- **FR-24** CSV reports with academic-year/state filters: enrolment vs intake by institution/programme/bucket; supernumerary utilisation (FN/OCI/PIO, NRI, TFW, lateral); over-cap flags; cross-institution duplicate cases; coverage; trust-tier distribution; institution risk ranking; data quality.
- **FR-25** Institution **risk score** = `0.35 anomaly + 0.25 duplicate_rate + 0.20 ring_flags_norm + 0.20 rule_violation_rate` (weights CONFIGURABLE). Always show the breakdown. Alerts use templates with i18n keys (no LLM), e.g. "3 admissions exceed the LATERAL bucket by 2 seats; see rows 41, 77."

### 5.7 Verification, consent, certificates (FR-26 to FR-34)
- **FR-26** `VERIFIER` searches by APAAR ID (optionally student ID) and submits a request with purpose and requested fields.
- **FR-27** Student sees the request (and a notification), grants (with field checkboxes), denies, or later revokes. Consent expires (default 7 days). Without consent nothing is revealed, not even "record exists" (CONFIGURABLE, default off).
- **FR-28** With consent the verifier receives only granted fields as a **signed record** (JSON, Ed25519) plus a QR.
- **FR-29** Selective disclosure: a different consent produces a different signed record. State in the README that this is **issuer-side minimisation (a fresh signed record per consent), not holder-side derivation**.
- **FR-30** **Offline verification:** QR encodes a compact signed payload (APAAR suffix, institution code, programme code, status, trust tier, issue date, expiry, nonce, key ID, signature). Public page `/verify` verifies the Ed25519 signature **in the browser** with the published public key and no API call. When online it also checks the revocation list.
- **FR-31** Revocation: signed, downloadable list; revoking consent or an upheld dispute revokes outstanding records.
- **FR-32** Student-visible **access log**: who requested, purpose, fields shared, when, outcome.
- **FR-33** Verification outcome states: valid, invalid, expired, revoked.
- **FR-34** **Certificate view:** printable sample degree certificate (HTML + browser print) showing **APAAR ID next to Student ID** and a verification QR.

### 5.8 Security, privacy, audit (FR-35 to FR-38)
- **FR-35** **Audit log** append-only and hash-chained: `hash = SHA256(prev_hash || canonical_json(entry))`. `GET /audit/verify` recomputes and reports the first broken entry. Audit rows are written in the same DB transaction as the action; chain ordering under an advisory lock. Demo helper tampers a row to show detection.
- **FR-36** All endpoints authenticated except `/verify`, `/health`, `/.well-known/public-key`, `/revocations`. RBAC enforced server-side.
- **FR-37** **Privacy-preserving duplicate token:** `identity_token = HMAC-SHA256(pepper, normalise(name) | dob | apaar_id)` and a `contact_token` per phone/email/address. Institution-facing APIs only ever get a boolean "possible elsewhere" and never another institution's data. **README must state the honest limit:** fuzzy matching runs on plaintext inside the AICTE trust boundary; tokens protect exact matching and institution-facing views; this is not private set intersection.
- **FR-38** Field-level encryption at rest (Fernet) for phone, email, address, sponsor and OCI number. Logs never contain raw PII. Passwords bcrypt.

### 5.9 Integration (FR-39, FR-40)
- **FR-39** OpenAPI docs at `/docs`; per-institution API keys for ERP systems (hashed, scoped to upload endpoints); a `scripts/sample_client.py` that uploads via API key.
- **FR-40** Adapter interfaces `ApaarClient` and `AuthorityClient` with mock implementations.

### 5.10 Demo and tooling (FR-41 to FR-43)
- **FR-41** `POST /demo/seed` generates a deterministic national dataset (default 40 institutions, about 20,000 students; `SEED=42`).
- **FR-42** **Fraud-injection demo mode** (AICTE_ADMIN only): (a) over-cap at one programme, (b) cross-institution duplicates with name variants, (c) shared-phone ring, (d) impossible timeline, (e) late-admission burst, (f) headcount-only institution, (g) a genuine transfer (must NOT raise a case). Each must be visible on the dashboard within seconds.
- **FR-43** **Concurrency demo:** endpoint/script fires N parallel admissions at a nearly-full bucket and shows consistent counts, with exactly the remaining seats clean and the rest flagged.

---

## 6. Non-functional requirements

| ID | Category | Requirement | Verify |
|---|---|---|---|
| NFR-1 | Security | JWT, RBAC on every route, bcrypt, login rate limiting, restricted CORS, Pydantic validation, parameterised queries only | per-role and negative tests |
| NFR-2 | Privacy | Consent for verification; minimisation; PII encrypted at rest; no PII in logs; say "aligned with DPDP Act 2023 and DPDP Rules 2025", never "compliant" | README checklist |
| NFR-3 | Integrity | Hash-chained audit; signed records; append-only versions | audit verify, signature tests |
| NFR-4 | Scalability | 20k records and 40 institutions comfortably on a laptop (tested up to 50k). Blocking prevents all-pairs comparison. Indexed queries, batched inserts | `scripts/perf.py` |
| NFR-5 | Performance | Single-record rule validation under 300 ms p95; 10k-row dry run under 30 s; dashboard queries under 1 s via cached aggregates refreshed on write | perf script |
| NFR-6 | Reliability | Seat Ledger atomic and correct under concurrency; idempotent uploads; versioned migrations | concurrency test |
| NFR-7 | Explainability | Every ML flag shows evidence: matched fields and scores; top features; ring members | UI review |
| NFR-8 | Human oversight | No automatic rejection, deletion, linking or status change from ML | test |
| NFR-9 | Usability | Plain-language messages with inline fix suggestions; keyboard-accessible forms; 1280px+ and tablet | manual |
| NFR-10 | Interoperability | OpenAPI, JSON + CSV, adapters, API keys | `/docs`, sample client |
| NFR-11 | Observability | Structured JSON logs, request IDs, `/health` and simple JSON counters | manual |
| NFR-12 | Maintainability | Rules and quotas as versioned config; typed code; a test per rule | `make test` |
| NFR-13 | Portability | `docker compose up` on Linux/macOS/Windows; no cloud dependency | fresh-clone test |
| NFR-14 | Testability | Deterministic seeds; unit tests for rules, ledger, signing, hash chain, tiers | pytest |

---

## 7. Data model

### 7.1 Core tables (PostgreSQL)

```
institutions(id, code UNIQUE, name, state, district, type, approved_flag, created_at)
programmes(id, canonical_code UNIQUE, name, level[UG|PG|DIPLOMA|PGDM|MCA|...], duration_years)
institution_programmes(id, institution_id, programme_id, academic_year, approved_intake INT,
    declared_enrolment INT,                       -- headcount reported at approval (coverage meter)
    shift[REGULAR|SECOND|PART_TIME], UNIQUE(institution_id, programme_id, academic_year, shift))
quota_rules(id, key, value_json, description, version, effective_from, updated_by)

users(id, email UNIQUE, password_hash, role, institution_id NULL, student_apaar_id NULL, active)
api_keys(id, institution_id, key_hash, label, last_used_at, active)

students(id, apaar_id UNIQUE, name_enc, name_norm, dob, gender, identity_token, created_at)
student_records(
  id, student_id, institution_id, institution_programme_id, student_college_id,
  admission_date, end_date NULL,
  admission_category[GENERAL|SC|ST|OBC|EWS|TFW|NRI|FN|OCI|PIO|CIWGC|OTHER],
  entry_type[REGULAR|LATERAL], mode[FULL_TIME|PART_TIME],
  ledger_key[APPROVED|SUPERNUMERARY_FN_OCI_PIO_CIWGC|SUPERNUMERARY_TFW|NRI|LATERAL],
  over_cap BOOL DEFAULT false, over_cap_reviewed BOOL DEFAULT false,
  nationality, is_nri BOOL, nri_sponsor_enc, is_oci BOOL, oci_number_enc, is_foreign_national BOOL,
  status[PENDING_APPROVAL|ACTIVE|NEEDS_CORRECTION|REJECTED|WITHDRAWN|GRADUATED|DROPPED],
  trust_tier SMALLINT DEFAULT 0,                  -- 0..3
  phone_enc, email_enc, address_enc, contact_token,
  current_version, batch_id, created_at, updated_at)
student_record_versions(id, student_record_id, version, data_json, changed_by, changed_at, reason)
trust_events(id, student_record_id, from_tier, to_tier, reason, actor, ts)
enrolment_confirmations(id, student_record_id, status[PENDING|CONFIRMED|DISPUTED|EXPIRED],
                        requested_at, expires_at, decided_at)

upload_batches(id, institution_id, maker_id, checker_id NULL,
    status[DRAFT|DRY_RUN|PENDING_APPROVAL|APPROVED|REJECTED],
    row_count, error_count, warning_count, created_at, decided_at, decision_note)
upload_chunks(id, upload_id, chunk_index, payload, received_at)

seat_ledger_entries(id, institution_programme_id, ledger_key, delta INT, student_record_id, batch_id, created_at)
seat_ledger_balances(institution_programme_id, ledger_key, used INT, cap INT,
                     PRIMARY KEY(institution_programme_id, ledger_key))   -- excess = max(0, used - cap)

validation_results(id, batch_id, student_record_id NULL, row_number, code, severity, field,
                   message_key, message_en, evidence_json, suggested_fix_json, resolved BOOL, resolution_note)
cases(id, type[RULE_WARNING|SEAT_OVER_CAP|DUP_EXACT_CROSS|DUP_FUZZY|ANOMALY|RING|TIMELINE|STUDENT_DISPUTE],
      visibility[INSTITUTION|AICTE_ONLY], priority, severity,
      status[OPEN|CONFIRMED|DISMISSED|ESCALATED|RESOLVED], subject_ids_json, score FLOAT,
      evidence_json, explanation_json, assigned_to, decided_by, decided_at, decision_note, created_at)
ml_feedback(id, case_id, label, features_json, created_at)

consent_requests(id, student_id, verifier_id, purpose, requested_fields_json,
    status[PENDING|GRANTED|DENIED|EXPIRED|REVOKED], granted_fields_json, expires_at, decided_at, created_at)
verification_events(id, consent_id, verifier_id, student_id, fields_json, outcome, signed_record_json, created_at)
signing_keys(id, key_id, public_key, private_key_enc, active, created_at)
revocations(id, record_hash, reason, revoked_at)
notifications(id, user_id, kind, payload_json, read, created_at)
audit_log(id BIGSERIAL, ts, actor_id, actor_role, action, entity_type, entity_id, detail_json, prev_hash, hash)
```

Indexes: `students(apaar_id)`, `student_records(institution_id, status)`, `student_records(contact_token)`, `student_records(trust_tier)`, trigram index on `students.name_norm`, `validation_results(batch_id)`, `cases(status, type, visibility)`.

`name_norm` is plaintext **inside the AICTE trust boundary** (needed for blocking) and is never returned by institution-facing APIs.

### 7.2 CSV upload columns
`apaar_id, student_college_id, full_name, dob(YYYY-MM-DD), gender, programme_code, shift, admission_date, admission_category, entry_type, mode, nationality, is_nri, nri_sponsor, is_oci, oci_number, is_foreign_national, phone, email, address`

### 7.3 Seat buckets and quota rules (CONFIGURABLE, **VERIFY** against the current AICTE Approval Process Handbook)

**What a bucket is:** a separate pool of seats with its own cap. Each admission lands in exactly one bucket, so over-admission is judged per seat type, not as one total.

Bucket mapping for a record:
```
entry_type == LATERAL                          -> LATERAL
category in (FN, OCI, PIO, CIWGC)              -> SUPERNUMERARY_FN_OCI_PIO_CIWGC
category == TFW                                -> SUPERNUMERARY_TFW
category == NRI                                -> NRI
otherwise                                      -> APPROVED
```
Caps per `institution_programme`:
- `APPROVED`: `approved_intake` (excludes supernumerary seats by definition).
- `SUPERNUMERARY_FN_OCI_PIO_CIWGC`: `floor(approved_intake * 0.15)`, **default 15%, VERIFY**. One-third reserved for CIWGC with reversion between pools if unfilled (document the simplification if not modelled). REGULAR shift only.
- `SUPERNUMERARY_TFW`: configurable percentage, default must be marked **VERIFY**.
- `NRI`: per institution/programme cap config, default 0 unless set (so an unapproved NRI admission is flagged).
- `LATERAL`: configurable, default `ceil(approved_intake * 0.20)` marked **VERIFY**.
Reservation categories (SC/ST/OBC/EWS) are tracked as **utilisation counts** inside APPROVED, with caps only where configured. Store everything in `quota_rules` (versioned, `effective_from`); changing a rule recomputes `cap` values; no code change.

**Atomic admission algorithm (flag mode, FR-10):**
```
BEGIN;
SELECT used, cap FROM seat_ledger_balances
  WHERE institution_programme_id = :ip AND ledger_key = :key FOR UPDATE;
used := used + 1;  over := (used > cap);
INSERT seat_ledger_entries(delta=+1, ...);
UPDATE seat_ledger_balances SET used = used;
UPDATE student_records SET over_cap = :over;
IF :over THEN upsert case SEAT_OVER_CAP (AICTE_ONLY) with bucket, cap, used, excess;
COMMIT;
```
Release (rejection or correction): same lock, `used := used - 1`, insert delta -1, refresh the open case's excess or close it when `used <= cap`. Withdrawals/graduation do not free approved-intake seats retroactively (document this simplification).

---

## 8. API (REST, JSON; prefix `/api/v1`)

Auth: `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me`.

Records and uploads:
- `GET /institutions`, `GET /institutions/{id}/seats` (buckets, caps, used, excess, coverage)
- `POST /students`, `PUT /students/{apaar_id}/records/{id}`, `GET /students/{apaar_id}` (scoped)
- `POST /uploads`, `PUT /uploads/{id}/chunks/{n}`, `POST /uploads/{id}/finalize`
- `POST /uploads/{id}/dry-run` (row report + projected bucket impact)
- `POST /uploads/{id}/submit`, `POST /batches/{id}/approve|reject`
- `GET /templates/students.csv`

Validation, cases, trust:
- `POST /validate/record`
- `GET /cases?type=&status=` (institution users get only `INSTITUTION`-visible), `GET /cases/{id}`, `POST /cases/{id}/decision`
- `GET /students/me/enrolments`, `POST /enrolments/{id}/confirm|dispute`
- `GET /dashboards/trust-tiers?institution_id=`

Dashboards and reports:
- `GET /dashboards/aicte`, `GET /dashboards/institution/{id}`, `GET /institutions/{id}/health-score`, `GET /institutions/risk-ranking`, `GET /coverage?institution_id=`
- `GET /reports/{name}?format=csv&academic_year=&state=`
- `GET /events/stream` (SSE)

Verification:
- `POST /verify/requests`, `GET /consent/requests`, `POST /consent/{id}/grant|deny|revoke`
- `GET /verify/records/{consent_id}`, `GET /verify/qr/{consent_id}.png`
- `GET /.well-known/public-key`, `GET /revocations`, public page `/verify`
- `GET /students/me/access-log`, `GET /certificates/{record_id}`

Admin and ops:
- `GET /audit`, `GET /audit/verify`
- `GET /rules`, `PUT /rules/{key}` (admin)
- `POST /demo/seed`, `POST /demo/inject/{scenario}`, `POST /demo/retrain`, `POST /demo/concurrency-test`, `POST /demo/tamper-audit`
- `GET /evaluation`, `POST /evaluation/run`
- `GET /mock/apaar/{id}` (MOCK), `GET /health`

Error shape: `{ "error": {"code": "...", "message_en": "...", "message_key": "...", "details": {...}} }`.

---

## 9. ML and analytics specification

General: CPU only; fixed seeds; each model has a model-card section in `docs/ml.md` (data, features, metrics, limits); thresholds configurable.

### 9.0 Name normalisation (`services/names.py`, used by ML-1 and the registry check)
Unicode NFKC, lowercase, strip honorifics (shri, smt, mr, mrs, dr, kumari), remove punctuation, **transliterate Devanagari and other Indic scripts to Latin** (`indic-transliteration` or `unidecode` fallback), apply a configurable variant table (mohammed/muhammad/mohd, sarma/sharma, etc.), expand/handle initials, produce Double Metaphone keys. This is what we describe as **"transliteration-aware (multilingual) name matching"**. If `ENABLE_LABSE=true`, add a LaBSE cosine feature; otherwise the README must say LaBSE is not used.

### 9.1 ML-1 Fuzzy cross-institution duplicate matcher
- **Stage 1 blocking:** candidates share at least one of: phonetic key of surname + birth year; first 3 chars of normalised name + DOB; same `contact_token`; `pg_trgm` similarity on `name_norm` above 0.5. Never compare all pairs.
- **Stage 2 classifier:** LightGBM on pair features: Jaro-Winkler, token-sort and token-set ratios, initials-aware match, phonetic match flags, DOB exact/edit distance/day-month swap, same gender, same programme code, **enrolment interval overlap**, phone/email match, address similarity, APAAR equal/one-missing/edit distance.
- **Calibration:** isotonic or Platt on a validation split; report a reliability curve and expected calibration error.
- **Transfer-aware rule (deterministic, applied before routing):** if the other enrolment's `end_date <= new admission_date`, treat as a transfer (INFO), no case. Include genuine transfers as hard negatives in training.
- **Routing:** per FR-14. Evidence shown: matched fields and per-field scores.
- **Training data:** synthetic pairs. Positives are corrupted copies of the same person; negatives include hard negatives (same surname and birth year, genuine transfers). **Train on a subset of corruption types and evaluate on held-out types** (see section 14). Reviewer decisions append to `ml_feedback`.

### 9.2 ML-2 Institution anomaly detector
- **Features** per institution, programme group and year, **peer-normalised** against the same programme group, state and intake band: fill ratio, share of admissions in the last 7 days of the window, category-mix distance from peers (Jensen-Shannon), supernumerary ratio, lateral share, warning share, year-on-year enrolment change, APAAR-mismatch share, **coverage ratio**.
- **Model:** `IsolationForest` (contamination default 0.05, fixed seed), output normalised to 0-1.
- **Explanation:** try SHAP `TreeExplainer`; if it fails on the installed versions, fall back to **per-feature peer z-scores** and label them as such. Never claim SHAP when the fallback is used.

### 9.3 ML-3 Shared-identity ring detector
Graph: records and contact tokens. Connected components, then Louvain on large components. Flag communities per FR-16. Explanation: masked shared identifier, number of records, institutions involved (AICTE-only view).

### 9.4 ML-4 Programme-name normaliser
TF-IDF character n-grams (2-4) with cosine similarity against the canonical programme list; return the top 3 with scores; below 0.8 requires user confirmation.

### 9.5 Deterministic timeline checks
Per APAAR ID build the enrolment timeline. Flag `TIMELINE_OVERLAP` (two overlapping FULL_TIME enrolments, after transfer logic), `TIMELINE_TOO_SHORT` (graduation earlier than admission plus minimum duration), `TIMELINE_LATERAL_NO_PRIOR` (lateral entry with no prior completed diploma/degree record), `TIMELINE_INVALID_DATE` (admission after programme end or in the future). Cross-institution timeline cases are AICTE-only.

### 9.6 Institution risk score
As FR-25, transparent breakdown. Note in docs that a supervised model needs real audit outcomes; do not train one on invented labels.

---

## 10. Synthetic data generator

Deterministic (`SEED=42`). Produces:
- 40 institutions across 8 states, 3-8 programmes each, approved intakes 60/120/180/240, second shift, PGDM/MCA mix, and `declared_enrolment` per programme.
- MOCK APAAR registry: 12-digit IDs with a clearly synthetic prefix (note that real structure/checksum is not modelled), Indian names with surname collisions, transliteration variants and some Devanagari-script names, DOB, gender.
- Students with realistic category mix and small shares of FN/OCI/PIO/NRI/TFW/lateral.
- 25-30% of institutions **headcount-only** (records received cover only 30-60% of declared).
- **Injectable scenarios:** (1) over-cap in a bucket; (2) cross-institution duplicates with corruptions: initials, swapped name order, one-letter typos, transliteration variants, DOB day-month swap, missing or wrong APAAR ID; (3) shared-phone ring of 25-60 records; (4) impossible timelines; (5) late-admission burst (70%+ in the last 5 days); (6) headcount-only institution; (7) genuine transfers (must not raise cases).
- Ground-truth labels saved for evaluation. Corruption types are tagged so train/held-out splits can be made by type.

---

## 11. Frontend (screens)

1. **Login** with role-based redirect. i18n scaffolding only (English).
2. **Institution Dashboard:** seat utilisation per **bucket** with caps and excess, **coverage meter**, **trust-tier split**, Data Health Score gauge, submission status, recent errors, pending cases.
3. **Upload Wizard:** download template, chunked upload with resumable progress, **dry-run report** (row table, severity filter, suggested fixes, **projected bucket impact panel**), submit for approval.
4. **Student Record Form:** live validation, programme suggestion, APAAR lookup status.
5. **Approvals (checker/registrar):** batch list, what will enter the repository, approve/reject with note.
6. **AICTE Dashboard:** KPI tiles; enrolment vs intake; state/programme drill-down; **over-cap flags**; **coverage distribution**; **enrolment by trust tier**; open cases by type; risk leaderboard with breakdown; duplicate-enrolment list; live SSE; demo-mode panel (admin only) with injection buttons and concurrency test.
7. **Review Queue:** case list and detail with evidence and explanation (matched-fields table, feature bars, ring graph or member table), type-specific decision buttons, notes. Institution users see only their own `INSTITUTION`-visible cases.
8. **Reports:** pick report, filters, preview, CSV export.
9. **Verifier Portal:** search, request with purpose/fields, status tracker, signed record and QR once granted.
10. **Student Portal:** my enrolments with **Confirm / Not me**, pending consent requests (grant with field checkboxes, deny, revoke), access log.
11. **Public Verify page** (`/verify`): paste or scan the payload, verify Ed25519 **in the browser** with the published public key; show valid/invalid/expired/revoked. Must work with the network disconnected after the page and key are loaded.
12. **Certificate view:** printable certificate with APAAR ID next to Student ID and QR.
13. **Audit & Rules (admin):** audit table with "Verify integrity" button; quota rules editor with version history.
14. **Evaluation page (admin):** metrics from section 14 with charts and a "Run evaluation" button.

UI notes: Tailwind, calm and accessible, loading and empty states, toasts use API `message_en`. Use the words **"flagged"** and **"held for review"**, never "blocked" or "rejected", for over-cap and ML outcomes.

---

## 12. Security implementation details

- Passwords bcrypt. JWT access 15 min, refresh 7 days; roles in claims.
- `require_roles(...)` dependency on every route; institution scoping through one query-layer helper; `AICTE_ONLY` visibility enforced in the same helper.
- Rate limiting: in-memory token bucket on login (document that production needs a shared store).
- Secrets from env (`.env.example`): `JWT_SECRET`, `FERNET_KEY`, `TOKEN_PEPPER`, `SIGNING_KEY_PASSPHRASE`.
- Signing: Ed25519 keypair generated at first start, public key at `/.well-known/public-key`, private key encrypted in DB. Sign canonical JSON (sorted keys, no whitespace) including `key_id`, `issued_at`, `expires_at`, `nonce`.
- Privacy tokens per FR-37, with the honest limitation documented.
- `docs/security.md`: OWASP ASVS-style checklist naming the exact items implemented (authentication, access control, input validation, logging).

---

## 13. Testing and acceptance criteria

Automated (pytest):
- **Rules:** one pass and one fail test per code in 5.2 and 9.5.
- **Seat Ledger concurrency:** bucket with 5 seats left, 50 parallel admissions -> **exactly 5 clean and 45 flagged**, `used = initial + 50`, no lost updates, one `SEAT_OVER_CAP` case with correct excess. Releasing 3 records lowers excess by 3.
- **Lateral and supernumerary buckets:** records map to the right bucket; 125 admissions with 120 regular + 5 FN is clean; 125 regular flags APPROVED by 5.
- **Flag, not reject:** an over-cap upload stores the record and opens a case; nothing is dropped.
- **Idempotency:** re-submitting a batch creates no duplicates.
- **RBAC:** every role denied what it should be; institution A cannot read institution B; **institution users never receive `AICTE_ONLY` cases or other institutions' details**.
- **Maker-checker:** maker cannot approve own batch; rejection releases seats.
- **Trust tiers:** T0 -> T1 -> T2 -> T3 progression; "Not me" opens `STUDENT_DISPUTE` and does not raise the tier; material edit resets to T1.
- **Transfer-aware:** a genuine transfer yields INFO and no case; an overlapping enrolment yields a case.
- **Coverage:** known fixture gives the expected coverage percentage.
- **Signing:** valid verifies; altered field fails; expired fails; revoked fails; different consent gives different signed content; the browser verifier util passes the same vectors (Vitest).
- **Audit chain:** valid passes; one modified row detected at the right index.
- **ML:** duplicate matcher F1 >= 0.85 on the in-distribution synthetic holdout; calibration error reported; anomaly detector ranks injected anomalous institutions in the top 10% in at least 80% of seeded runs; ring detector finds all injected rings.
- **ML never mutates status:** after triage, no record status or tier changes because of an ML case.
- **Performance script:** prints the 10k-row dry-run time and dashboard query time.

Manual acceptance: the demo script (section 16) runs end to end on a fresh clone.

---

## 14. Evaluation module (backs the PPT claim "Measured in demo")

`backend/app/eval/run_eval.py`, exposed at `/evaluation` and the Evaluation page, writes `docs/evaluation.md`. Report:
1. **Duplicate matcher:** precision, recall, F1, PR-AUC, calibration error. Report **in-distribution** and **held-out corruption types** separately (train on initials, one-letter typos, swapped order, missing APAAR; hold out transliteration variants, DOB day-month swap and combined corruptions). Report honestly even if held-out is lower.
2. **Rules:** detection rate per injected fault (invalid APAAR, exact duplicate, over-cap, inconsistent category, timeline).
3. **Anomaly detector:** share of injected anomalous institutions in the top 10%; **ring detector:** recall of injected rings.
4. **Seat Ledger:** concurrency result (clean vs flagged counts).
5. **Performance:** 10k-row dry-run time, p95 single-record validation.
6. **Review-time saved (proxy):** `rows needing human review / total rows` per upload and nationally, plus an estimated manual effort comparison using a stated **ASSUMPTION** (e.g. 2 minutes per row for manual checking, configurable) clearly labelled as an estimate, not a measurement.
7. State at the top: metrics measure the **synthetic benchmark**, not real-world performance; reviewer feedback is the path to real labels.

---

## 15. Build order (phases; each ships backend + UI)

1. **Foundation.** Repo, Docker Compose, Postgres, FastAPI skeleton, config, auth + RBAC, Alembic, seed users/institutions/programmes. UI: login, role redirect, shell. *Done when:* all roles log in, `/docs` loads, RBAC tests pass.
2. **Data and mock registry.** Models, generator (all scenarios, tagged corruptions), MOCK APAAR registry, `/demo/seed`, name normaliser. *Done when:* seed creates 40 institutions and about 20k records with headcount-only institutions.
3. **Rule engine and Seat Ledger.** Rules, `quota_rules`, buckets incl. LATERAL, flag-mode ledger with release, timeline and transfer logic, validate endpoint, concurrency test. UI: Student Record Form. *Done when:* rule, bucket and concurrency tests pass.
4. **Upload pipeline, maker-checker, audit, tiers T0-T2.** Chunked upload, dry run with projected bucket impact and suggested fixes, submit, approve/reject, versioning, hash-chained audit, trust events. UI: Upload Wizard, Approvals, Institution Dashboard (buckets, tier split). *Done when:* a CSV flows from dry run to repository and rejection releases seats.
5. **ML triage.** Blocking, LightGBM + calibration, anomaly, rings, cases, AICTE-only visibility, review queue API, feedback, retrain. UI: Review Queue. *Done when:* ML test floors pass and institutions cannot see AICTE-only cases.
6. **Dashboards and reports.** Aggregates, SSE, coverage meter, tier split, risk score, CSV reports. UI: AICTE Dashboard. *Done when:* the dashboard updates when data changes.
7. **Student confirmation and verification.** Enrolment confirmation (T3), disputes, consent, signed records, QR, public key, revocation, access log, certificate, browser verifier. UI: Student Portal, Verifier Portal, `/verify`, Certificate. *Done when:* a QR verifies offline, tampering fails, and confirming raises T1/T2 to T3.
8. **Demo mode and evaluation.** Injection scenarios (including genuine transfer and headcount-only), concurrency demo, tamper-audit helper, evaluation module and page, README, docs, reset command. *Done when:* the demo script runs cleanly on a fresh clone.
9. **Hardening.** Login rate limits, security checklist, perf script, final full test run, README traceability table filled (PPT claim -> requirement -> code -> test).

**Never drop** (each is on a slide): Seat Ledger with buckets, dry run, maker-checker, trust tiers, coverage meter, duplicate matcher with transfer-awareness, anomaly detector, ring detector, hashed tokens, signed offline QR, access log, audit chain, certificate print, timeline checks, evaluation page.
**Drop first if time is short:** ring graph visual (keep the member table), report variety beyond 4 CSVs, SHAP (use z-scores), LaBSE option, API-key client sample.

---

## 16. Demo script (4 to 5 minutes)

1. AICTE_ADMIN: national dashboard with enrolment vs intake, **coverage meter** (point at a headcount-only institution), **trust-tier split**, open cases, risk leaderboard.
2. INST_MAKER: upload a CSV with deliberate faults; show the **dry-run report** with suggested fixes and the **projected bucket overflow**; fix and submit.
3. INST_CHECKER (registrar): approve (show a maker cannot approve their own batch).
4. **Concurrency demo** on a nearly full bucket: exactly the remaining seats are clean, the rest are **flagged and held for review**, counts consistent. AICTE officer opens the over-cap case and accepts an exception or requests correction.
5. **Inject cross-institution duplicates:** new cases appear within seconds; open one: matched fields, calibrated probability, human decision. Then **inject a genuine transfer** and show it produces no case. Show the institution only sees the neutral "possible enrolment elsewhere" message.
6. **Inject shared-phone ring** and **late-admission burst:** ring view and anomaly leaderboard with per-feature explanations.
7. STUDENT: **Confirm enrolment**, record rises to T3, dashboard tier split updates; show a "Not me" dispute opening a case.
8. VERIFIER requests; STUDENT grants limited fields; show signed record, QR and the student **access log**.
9. `/verify` in a private window with the network off: QR verifies; alter one character and it fails.
10. Audit log: **Verify integrity**, tamper a row, show detection.
11. Certificate with APAAR ID next to Student ID.
12. Evaluation page: precision/recall on injected faults (in-distribution and held-out) and the review-time proxy. Say plainly that these are synthetic-benchmark numbers.

---

## 17. Known limitations (state in README, do not hide)

- APAAR registry, authority and ABC/DigiLocker integrations and notifications are **MOCK**; real APAAR structure and checksum are not modelled.
- ML is trained on synthetic data; metrics measure the synthetic benchmark. Reviewer feedback is the path to real labels.
- Privacy tokens protect exact matching and institution-facing views only; fuzzy matching runs on plaintext inside the AICTE trust boundary. Not private set intersection.
- Selective disclosure is issuer-side minimisation (a fresh signed record per consent), not holder-side derivation.
- Name matching is transliteration-aware (Latin and Indic scripts, variant tables, phonetics); LaBSE is not used unless `ENABLE_LABSE=true`.
- Quota defaults (15% FN/OCI/PIO/CIWGC, TFW, LATERAL percentages) must be **verified** against the current AICTE Approval Process Handbook.
- Withdrawals and graduation do not free approved-intake seats retroactively.
- Single signing key in the DB; production needs HSM/KMS and rotation. Login rate limiting is in-memory.
- "Aligned with DPDP Act 2023 and DPDP Rules 2025" describes design intent, not a legal compliance certification.
- The review-time saving is an estimate based on a stated assumption, not a field measurement.

---

## 18. Reference facts (use for docs, not as code constants)

- APAAR: 12-digit lifelong student ID under NEP 2020, linked to the Academic Bank of Credits and DigiLocker.
- DPDP Rules 2025 were notified on 13 Nov 2025; obligations phase in over 12 to 18 months.
- AICTE defines Approved Intake as the maximum students in a course excluding supernumerary seats; up to 15% supernumerary over approved intake is allowed for FN/OCI/PIO/CIWGC (**VERIFY** in the current handbook).
- Cite in `docs/ml.md`: Fellegi & Sunter (1969); Liu, Ting & Zhou (2008); Ke et al. (2017); Blondel et al. (2008); Lundberg & Lee (2017); Feng et al. (2022). Standards: W3C Verifiable Credentials Data Model v2.0, RFC 8032, NIST AI RMF 1.0.

---

## 19. Definition of done

- `docker compose up` brings up db, api, web; demo accounts work for every role.
- FR-1 to FR-43 and NFR-1 to NFR-14 are implemented or listed as deferred in the README with a reason.
- Every PPT claim P1 to P24 (section 1.2) is demonstrable and mapped in the README traceability table.
- Section 13 tests pass; ML floors met; evaluation report generated.
- The section 16 demo runs cleanly on a fresh clone.
- README includes setup, demo accounts, a Mermaid architecture diagram, the traceability table and the limitations list.
