# APAAR Ledger (prototype)

SIH 2026, problem statement SIH26263 (AICTE, Regulation Bureau). Team Spitzen Squad (P121).

> We do not just collect student data; we make every enrolment number provable, bucket by
> bucket and student by student, without exposing anyone's data.

The authoritative build spec is [`APAAR_Ledger_Build_Spec.md`](APAAR_Ledger_Build_Spec.md).
Design decisions are logged in [`docs/decisions.md`](docs/decisions.md).

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Foundation: Compose, Postgres, FastAPI, config, JWT auth + RBAC, Alembic, seed, login UI + role shells | **Done** |
| 2 | Data model (all spec 7.1 tables), synthetic national generator with tagged scenarios, **MOCK** APAAR registry, `POST /demo/seed`, name normaliser, field encryption | **Done** (backend only; no UI in this phase) |
| 3 | Rule engine (FR-6 to FR-13, timelines, transfer-aware rule), flag-mode Seat Ledger with buckets incl. LATERAL, release and FR-11 decisions, rules-as-data admin, `POST /validate/record`, replay of the seeded data, **Student Record Form** UI | **Done** |
| 4 | Chunked CSV upload, dry run with suggested fixes and projected bucket impact, submit, maker-checker approve/reject (rejection releases seats), record versioning, trust tiers T0-T2, hash-chained audit log; **Upload Wizard**, **Approvals**, **Institution Dashboard** (buckets, tier split) and **Audit log** UI | **Done** |
| 5 to 9 | ML triage, dashboards, verification, demo mode and evaluation, hardening | Not started |

## Quick start

```bash
docker compose up --build        # or: make up
```

| Service | URL |
|---|---|
| Web UI | http://localhost:5173 |
| API docs (OpenAPI) | http://localhost:8000/docs |
| Health | http://localhost:8000/health |
| Postgres | localhost:5432 (`apaar` / `apaar`) |

On start, the `api` container applies Alembic migrations and loads the idempotent seed automatically.
On the **first** start (no student records yet) it also generates the synthetic national dataset
(40 institutions, about 20,000 students, `SEED=42`); this takes roughly 40-60 seconds (generation, load and the Phase 3 rule/ledger replay) before the API
answers. To override the dev defaults, copy `.env.example` to `.env`. To wipe the data, run `make reset`.

### Demo accounts

All demo accounts use the password **`Demo@2026`** (env `DEMO_PASSWORD`).

| Role | Email | Lands on |
|---|---|---|
| `AICTE_ADMIN` | admin@aicte.demo | `/aicte` |
| `AICTE_REVIEWER` | reviewer@aicte.demo | `/aicte` |
| `INST_MAKER` | maker@inst1.demo (DEMO-INST-001) | `/institution` |
| `INST_CHECKER` | checker@inst1.demo (DEMO-INST-001) | `/institution` |
| `VERIFIER` | verifier@employer.demo | `/verifier` |
| `STUDENT` | student@student.demo (APAAR `990274831650`; MOCK registry name "Ananya Rao", enrolled at DEMO-INST-001) | `/student` |

Seeded institutions and programmes are **synthetic demo data**. They are not real institutions
and do not use real AICTE programme codes.

## Development

```bash
make test     # pytest inside the api container (uses the apaar_test database)
make lint     # ruff + black (backend), eslint (frontend)
```

To run without Docker you need Python 3.11+, Node 20+ and a Postgres 16 server:

```bash
cd backend && pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://apaar:apaar@localhost:5432/apaar
alembic upgrade head && python -m app.db.seed && uvicorn app.main:app --reload
TEST_DATABASE_URL=postgresql+psycopg://apaar:apaar@localhost:5432/apaar_test pytest

cd frontend && npm install && npm run dev   # proxies /api to http://localhost:8000
```

## API (Phases 1-4)

All routes are under `/api/v1` and need a bearer token, except `/health` and the login/refresh endpoints.

| Method | Path | Roles |
|---|---|---|
| POST | `/auth/login` (OAuth2 form, `username` = email) | public |
| POST | `/auth/refresh` | public (needs a refresh token) |
| GET | `/auth/me` | any authenticated user |
| GET | `/institutions`, `/institutions/{id}`, `/institutions/{id}/programmes` | AICTE roles (all); institution roles (own institution only) |
| GET | `/programmes` | AICTE and institution roles |
| GET | `/users` | `AICTE_ADMIN` |
| GET | `/mock/apaar/{apaar_id}` (**MOCK** registry: name, DOB, gender; 404 unknown, 422 malformed) | AICTE roles |
| POST | `/demo/seed?seed=42&institutions=40&students=20000` (wipe, regenerate and replay the synthetic dataset through the rule engine and Seat Ledger; synchronous, ~35-45 s) | `AICTE_ADMIN` |
| POST | `/validate/record` (dry run of every rule on one record: outcomes, APAAR match booleans, read-only seat-bucket projection; writes nothing) | AICTE roles (with `institution_id`); institution roles (own institution) |
| GET | `/validate/codes` (rule codes, severities, i18n message keys) | AICTE and institution roles |
| POST | `/students` (create one record: rules, then store as `PENDING_APPROVAL` in a one-row FORM batch, take a seat in flag mode, T0 -> T1 -> T2; ERRORs return 422 `RECORD_HAS_ERRORS` with the fix list) | `INST_MAKER` (own institution) |
| PUT | `/students/{apaar_id}/records/{id}` (append a version; a material edit of name, DOB, programme or bucket resets the tier to T1, re-runs the checks and moves the seat if the bucket changed; a `NEEDS_CORRECTION` record goes back to `PENDING_APPROVAL` in a CORRECTION batch) | `INST_MAKER` (own institution) |
| GET | `/students/{apaar_id}` (the person's records at the caller's institution) | institution roles |
| GET | `/templates/students.csv` (spec 7.2 columns) | AICTE and institution roles |
| POST | `/uploads` `{filename, total_chunks}`; `PUT /uploads/{id}/chunks/{n}` (raw bytes, re-sendable until finalize); `GET /uploads/{id}` (received chunks, for resume); `POST /uploads/{id}/finalize` (idempotent) | `INST_MAKER` (own uploads); GET also checker/AICTE |
| POST | `/uploads/{id}/dry-run` (per-row report with suggested fixes + projected seat-bucket impact; writes nothing to records or the ledger); `GET /uploads/{id}/report?severity=` | `INST_MAKER` (own uploads) |
| POST | `/uploads/{id}/submit` `{exclude_error_rows, justification, row_justifications}` (rules -> store T0 -> seats in flag mode -> T1/T2 -> `PENDING_APPROVAL`; idempotent) | `INST_MAKER` (own uploads) |
| GET | `/batches?status=`, `/batches/{id}`, `/batches/{id}/records` (what will enter the repository) | institution roles (own institution); AICTE roles |
| POST | `/batches/{id}/approve`, `/batches/{id}/reject` `{note}` (approve -> records `ACTIVE`; reject -> `REJECTED`, seats released, over-cap cases refreshed; the batch's maker can never decide it) | `INST_CHECKER` (own institution) |
| GET | `/dashboards/institution/{id}` (buckets with caps/excess, tier split, submissions, recent errors, own open cases); `/dashboards/trust-tiers?institution_id=` | AICTE roles; institution roles (own only) |
| GET | `/audit?action=&entity_type=&entity_id=`, `/audit/verify` (recompute the hash chain, report the first broken entry) | AICTE roles |
| GET | `/institutions/{id}/seats?academic_year=` (per programme row: every bucket's cap, used, excess; records received vs declared) | AICTE roles; institution roles (own only) |
| GET | `/cases?type=&status=&institution_id=`, `/cases/{id}` (institution users only ever get their own `INSTITUTION`-visible cases, never `AICTE_ONLY`) | AICTE and institution roles |
| POST | `/cases/{id}/decision` `{action: ACCEPT_EXCEPTION or REQUEST_CORRECTION, note, record_ids?}` (FR-11, `SEAT_OVER_CAP` cases; other types arrive with the Phase 5 review queue) | AICTE roles |
| GET | `/rules` (active value, version history, VERIFY flags) | `AICTE_ADMIN` |
| PUT | `/rules/{key}` `{value_json, description?, effective_from?}` (appends a version; bucket rules recompute every seat cap, flag and over-cap case at once) | `AICTE_ADMIN` |
| GET | `/health` | public |

Errors use one shape: `{"error": {"code", "message_en", "message_key", "details"}}`.

## Synthetic national dataset (Phase 2)

`app/synth/generator.py` builds a deterministic dataset (default `SEED=42`); `app/synth/loader.py`
writes it. Default run (seed 42): **40 institutions** in 8 states (the 3 demo institutions plus
`SYN-INST-001..037`), ~275 institution-programme rows (3-8 programmes per institution, intakes
60/120/180/240, second-shift and part-time rows, PGDM and MCA), **~20,570 student records**
(~20,070 for 2025-26 plus history records for lateral entrants and timelines), ~20,000 students and
~20,100 MOCK registry entries. **11 of 40 institutions (27.5%) are headcount-only**: they declare
their full enrolment but only 30-60% of the student records arrive.

Every injected scenario is stored with ground truth in `ground_truth_labels` (AICTE-internal) so
later phases and the evaluation module can score detection:

| Scenario | What is generated (default size) |
|---|---|
| `OVER_CAP` | APPROVED bucket +5, LATERAL bucket +2, an unapproved NRI admission (cap 0) +2 |
| `DUPLICATE` | 300 cross-institution duplicate groups (10% with two copies). APAAR same (20%), missing or wrong; corruptions tagged per copy: `INITIALS`, `SWAPPED_ORDER`, `TYPO`, `TRANSLITERATION` (Devanagari script or another romanisation), `DOB_DAY_MONTH_SWAP`, `APAAR_MISSING`, `APAAR_WRONG`, with a `train`/`heldout` split per spec 14 |
| `RING` | 2 shared-phone rings of 25-60 records across 2-4 institutions |
| `TIMELINE` | 8 each: overlapping full-time enrolments, too-short diploma before lateral entry, lateral entry with no prior record, invalid dates (future admission / end before admission) |
| `LATE_BURST` | 2 institutions with 70%+ of admissions in the last 5 days of the window |
| `HEADCOUNT_ONLY` | the 11 institutions above, with declared vs received counts |
| `TRANSFER` | 30 genuine transfers: withdrawn at A, admitted at B after the end date (must not raise a case) |
| `RULE_FAULT` | 5 each: invalid / suspicious APAAR, registry DOB mismatch, same-institution duplicate, OCI without number, FN with Indian nationality, NRI without sponsor, admission outside the window |
| `REGISTRY_VARIANT`, `NATURAL_SHARED_PHONE` | hard negatives: registry name in another script/spelling; siblings sharing a phone |

Seeded records are historical, already-approved data (status ACTIVE/WITHDRAWN/GRADUATED). The
generator writes no flags, cases or tiers; since Phase 3 the seed replays the records through the real
rule engine and Seat Ledger (see below): records without an ERROR rise to T1, and to T2 when the
MOCK registry matches; T3 arrives in Phase 7.

### Name matching ("transliteration-aware / multilingual")

`app/services/names.py` implements exactly this: Unicode NFKC; honorifics removed (Latin and
Devanagari); Indic scripts (Devanagari, Bengali, Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Kannada,
Malayalam) transliterated with `indic-transliteration` plus a schwa-deletion heuristic, other
non-ASCII text with `unidecode`; punctuation removed; romanisation folds (aa/ee/oo/w-v/iya); a
configurable variant table (mohammed/muhammad/mohd, sarma/sharma, laxmi/lakshmi, ...); initials
split and matched; Double Metaphone keys (`Metaphone` package). **LaBSE is not used**
(`ENABLE_LABSE=false`; no LaBSE code exists). Tamil-script transliteration is approximate.

## Rule engine and Seat Ledger (Phase 3)

*Rules decide.* `app/services/rule_engine.py` runs every deterministic check on a record and returns
institution-facing **outcomes** plus **AICTE-only case specs**. It reads through a `RuleContext`
(DB-backed with caches, or in-memory for tests) and writes nothing itself, so the same engine powers
the dry run, single-record creation and the replay of the seeded data. Each outcome has `code`,
`severity`, `field`, `message_key` (i18n, English values only), `message_en`, `evidence` and an
optional `suggested_fix`; stored outcomes go to `validation_results`.

| Code | Severity | Rule |
|---|---|---|
| `APAAR_INVALID` | ERROR | FR-6: missing, or not exactly 12 digits |
| `APAAR_SUSPICIOUS` | WARNING | FR-6: all digits the same, or an ascending/descending run of 6+ digits |
| `APAAR_NOT_FOUND` | ERROR | FR-7: not in the **MOCK** registry |
| `APAAR_MISMATCH` | WARNING | FR-7: name (transliteration-aware fuzzy score < 85) or DOB (exact) differs from the registry. Only booleans are returned, never registry data |
| `DUP_EXACT_SAME_INST` | ERROR | FR-8: same APAAR ID, institution, programme and year |
| `ENROLMENT_ELSEWHERE_NEUTRAL` | INFO | FR-9: the only thing an institution learns about a cross-institution duplicate or overlap. The `DUP_EXACT_CROSS` / `TIMELINE` case itself is `AICTE_ONLY` |
| `TRANSFER_DETECTED` | INFO | 9.1: an earlier enrolment ended (without completing) on or before this admission. Applied before any duplicate/overlap decision; **no case** |
| `SEAT_OVER_CAP` | WARNING | FR-10: the admission pushed its bucket over the cap; stored, flagged and held for review (AICTE-only case with bucket, cap, used, excess) |
| `FN_NATIONALITY_INVALID`, `FN_ID_DOCUMENT_MISSING`, `OCI_NUMBER_MISSING`, `NRI_SPONSOR_MISSING` | ERROR / WARNING / ERROR / ERROR | FR-12 category requirements |
| `ADMISSION_OUTSIDE_WINDOW`, `DOB_IMPLAUSIBLE` | WARNING | FR-12: academic-year admission window; plausible age for the programme level |
| `REQUIRED_FIELD_MISSING`, `FIELD_INVALID`, `PROGRAMME_NOT_APPROVED` | ERROR | FR-12: required fields, allowed values, programme in the institution's approved list (with suggestions) |
| `TIMELINE_OVERLAP`, `TIMELINE_TOO_SHORT`, `TIMELINE_LATERAL_NO_PRIOR` | WARNING | 9.5: overlapping full-time enrolments; graduation before admission + minimum duration; lateral entry with no completed prior record. Cross-institution findings become `AICTE_ONLY` `TIMELINE` cases |
| `TIMELINE_INVALID_DATE` | ERROR | 9.5: admission in the future, or end date before admission |
| `TIMELINE_CHECK_NEUTRAL` | INFO | neutral counterpart of a cross-institution timeline case |

ERRORs must be fixed before a record is saved; WARNINGs need a justification (Phase 4 upload flow);
INFO needs nothing. Cross-institution cases are keyed by an HMAC token of the APAAR ID
(`privacy_tokens.apaar_token`), never the raw ID.

**Seat Ledger (flag mode, `app/services/seat_ledger.py`).** Bucket mapping exactly as spec 7.3
(`LATERAL` first, then FN/OCI/PIO/CIWGC, TFW, NRI, else `APPROVED`). Caps come from the active
`quota_rules` version (all **VERIFY** defaults: FN/OCI/PIO/CIWGC 15% REGULAR shift only, TFW 5%,
LATERAL ceil 20%, NRI 0 unless set per programme row). `admit()` follows the spec 7.3 algorithm:
`SELECT ... FOR UPDATE` on the bucket's balance row, `used + 1`, a `+1` ledger entry, the record's
`over_cap` flag and, when over, an upsert of the bucket's `AICTE_ONLY` `SEAT_OVER_CAP` case. Nothing
is ever rejected. `release()` (correction / rejection) takes the same lock, writes `-1` and refreshes
or closes the case. The first `cap` seat holders are clean and the rest flagged, so flagged records
always equal the excess. FR-11: an AICTE officer either **accepts the exception** (flags marked
reviewed, case resolved with the note) or **requests correction** (records -> `NEEDS_CORRECTION`,
seats released, the institution's maker and checker notified through the MOCK `notifications` table).

**Concurrency (spec 13, P24).** `tests/test_seat_ledger.py` fires 50 parallel admissions (50 DB
connections released together by a barrier) at a bucket with 5 seats left: exactly 5 clean and 45
flagged, `used` = 15 + 50 = 65 with every running count 16..65 seen exactly once (no lost update), one
`SEAT_OVER_CAP` case with excess 45; releasing 3 flagged records in parallel lowers the excess to 42.

**Replay of the seeded data.** `POST /demo/seed` (and the first-start seed) now ends with
`app/synth/replay.py`: every seeded record runs through the rule engine and the Seat Ledger (same
algorithm, in admission order per bucket). Default run (seed 42): 20,573 records, 306 with an ERROR
(stay T0; the rest rise to T1), 9 over-cap admissions in exactly the 3 injected buckets, 60
`DUP_EXACT_CROSS` cases (the same-APAAR duplicate groups), 16 `TIMELINE` cases, 30 `TRANSFER_DETECTED`
with no case, every injected rule fault detected, and no ERROR/WARNING on any record outside an
injected scenario (`tests/test_phase3_replay.py` checks this against the ground-truth labels).
Seeded records keep their historical status (ACTIVE/WITHDRAWN/GRADUATED). Run it by hand with
`python -m app.synth.replay`.

**Student Record Form** (`/institution/records/new`, maker): live validation (debounced dry run),
APAAR lookup status (match booleans from the MOCK registry), a seat-bucket panel ("would be seat 119
of 120" or "flagged and held for review"), programme suggestions (exact/prefix/substring match on
the institution's approved list; the ML-4 TF-IDF mapper is Phase 5), inline per-field messages, and
Save (disabled while there are ERRORs).

## Upload pipeline, maker-checker, trust tiers and audit (Phase 4)

- **Upload Wizard** (`/institution/uploads`, maker): download the template, upload the CSV in 64 KB
  chunks with a progress bar (choosing the same file again resumes: received chunks are skipped),
  then the dry run: summary tiles, a row table with a severity filter, per-row findings with a
  suggested fix (e.g. "Did you mean 2007-04-02?" for `02/04/2007`), and the projected seat-bucket
  panel ("Rows 1, 2, 3: would exceed APPROVED by 3 ..."). Submit needs either no ERROR rows or
  "submit without the error rows", and a justification when any row has a WARNING.
- Rows of one file are also checked against each other (a student listed twice is caught in the
  dry run). The CSV and its chunks are stored Fernet-encrypted (they hold contact data).
- **Approvals** (`/institution/approvals`, checker): pending batches, what each will put into the
  repository (rows, bucket, flag, tier, findings), approve or reject with a note. A maker never
  decides their own batch (enforced by role and, separately, by `maker_id`; both tested).
- **Institution Dashboard** (`/institution/dashboard`): seat utilisation per programme and bucket
  with caps and excess, over-cap flags, trust-tier split, submission status, recent errors, the
  institution's own open cases. Coverage meter, Data Health Score and live SSE updates are Phase 6.
- **Trust tiers**: T0 on ingest, T1 when no rule ERROR, T2 when the APAAR ID matches the **MOCK**
  registry (fuzzy name + exact DOB); every step is a `trust_events` row. T3 is Phase 7.
- **Audit log** (`/aicte/audit`): every login (and failed login, without the typed email), upload
  step, submission, batch decision, record create/update, case decision, rule change and demo
  reseed is appended in the same transaction as the action, chained with
  `hash = SHA256(prev_hash || canonical_json(entry))` under a Postgres advisory lock. A trigger
  refuses UPDATE/DELETE; "Verify integrity" recomputes the chain and names the first broken entry.

## Mocked / not yet built

- **MOCK APAAR registry** (`mock_apaar_registry` table, `services/apaar_client.py`, `GET /mock/apaar/{id}`):
  synthetic people only; IDs are 12 digits with a `99` prefix, and the real APAAR structure and checksum are not modelled.
- All institutions, people, phone numbers (`9999...`) and emails (`@mail.example`) are synthetic.
- Notifications are rows in the `notifications` table (**MOCK** email/SMS); Phase 3 writes them for
  FR-11 correction requests. Authority/ABC/DigiLocker adapters arrive in later phases.
- Not yet built: ML triage and the review queue (Phase 5; submission leaves a hook where it runs),
  AICTE dashboard, coverage meter, SSE and CSV reports (Phase 6), student confirmation (T3),
  consent and verification (Phase 7), demo injection, `/demo/tamper-audit`, `/demo/concurrency-test`,
  the quota rules editor UI and evaluation (Phase 8), login rate limiting (Phase 9).
- Batch approval does not yet create enrolment confirmation requests for students (FR-19, Phase 7).

## Known limitations (so far)

- JWTs are stateless. Logout clears the tokens in the browser only, and tokens are stored in
  `localStorage` (prototype simplification; production would use httpOnly cookies).
- All demo accounts share one password. Secrets in `.env.example` and `docker-compose.yml` are dev-only defaults.
- PII fields are Fernet-encrypted at rest. If `FERNET_KEY` is empty a dev-only key is derived from
  `JWT_SECRET`; production needs a managed key (KMS/HSM) and rotation.
- Quota defaults (15% FN/OCI/PIO/CIWGC, 5% TFW, 20% LATERAL, NRI 0 unless set) are **VERIFY** defaults
  in the `quota_rules` table, not verified AICTE facts. The CIWGC one-third reservation is not modelled.
- Withdrawals and graduation do not free approved-intake seats retroactively (spec 7.3); only
  rejection or a correction request releases a seat.
- Timeline checks use projected end dates (admission + programme duration) when an end date is not
  reported, and a configurable 6-month tolerance for "too short". A lateral entrant's prior
  diploma may predate APAAR, so `TIMELINE_LATERAL_NO_PRIOR` is a WARNING, not an ERROR.
- A person row keeps the name/DOB first submitted for an APAAR ID; a later institution's spelling is
  kept (encrypted) in that record's version snapshot.
- The audit chain detects edits, deletions and reordering of existing entries, but it is not anchored
  outside the database: someone with owner rights who disables the append-only trigger and
  rewrites the whole chain from the edited row onward would not be detected. Production would
  periodically publish or sign the head hash.
- Failed logins are audited (without the typed email); until login rate limiting lands (Phase 9)
  a flood of failed logins also grows the audit log.
- The `pg_trgm` trigram index on `students.name_norm` is created only if the PostgreSQL server ships
  `pg_trgm` (the `postgres:16` image does).

The full list from spec section 17, the traceability table, the architecture diagram and the
demo script will be added as later phases land.
