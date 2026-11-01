# Progress summary — Phases 1-4 complete, Phases 5-9 not started

Build follows `APAAR_Ledger_Build_Spec.md` section 15's phase order. Each phase below is
committed on `master`; tests and lint were run against a real PostgreSQL 16 for every phase
(not SQLite — `SELECT ... FOR UPDATE` and `pg_trgm` need real Postgres).

## Phase 1 — Foundation (`310e9db`)
Repo scaffold, Docker Compose (db/api/web), FastAPI skeleton, JWT auth + RBAC (`require_roles`),
Alembic, seed (6 demo accounts, 3 institutions, 8 programmes), React login + per-role shell.
**63 tests pass.** Docker itself was never actually run (no Docker in the build sandbox) — only
the compose/Dockerfiles were written and validated by parsing; verified instead via a real
Postgres + uvicorn + Vite dev server, including headless-browser login checks for all 6 roles.

## Phase 2 — Data and mock registry (`c57d9e2`)
Full data model (all spec 7.1 tables, Fernet field encryption, HMAC privacy tokens), synthetic
generator (deterministic, SEED=42: 40 institutions / ~20,573 student records, 11 headcount-only
institutions, tagged scenarios — over-cap, 300 duplicate groups, 2 rings, timeline faults,
late-admission bursts, 30 genuine transfers — with ground-truth labels and a train/held-out
split for later evaluation), MOCK APAAR registry (`/mock/apaar/{id}`), name normaliser
(Devanagari transliteration, honorifics, variant table, Double Metaphone). **132 tests pass.**
Seeded records were deliberately left at trust tier T0 with no ledger/case state — that's what
Phase 3 replays through the real engine.

## Phase 3 — Rule engine and Seat Ledger (`0039117`)
Deterministic rule engine (FR-6 to FR-13, timeline checks, transfer-aware logic applied before
any duplicate/overlap case), Seat Ledger with the spec's exact atomic admit/release algorithm
(buckets: APPROVED, SUPERNUMERARY_FN_OCI_PIO_CIWGC, SUPERNUMERARY_TFW, NRI, LATERAL; caps from
versioned `quota_rules`), `POST /validate/record`, `POST /students`, admin rules endpoints,
cases API (SEAT_OVER_CAP decisions), replay of all seeded data through the real pipeline, Student
Record Form UI. **246 tests pass**, including the spec's exact concurrency scenario: a bucket
with cap 20 / used 15, 50 parallel admissions → **exactly 5 clean, 45 flagged**, used ends at 65,
no lost updates, one case with excess 45; releasing 3 records drops excess to 42.

## Phase 4 — Upload pipeline, maker-checker, audit, trust tiers T0-T2 (`2bdcc78`)
Chunked/resumable CSV upload, dry-run (per-row report + suggested fixes + in-memory projected
seat-bucket impact, writes nothing), submit (idempotent, runs the Phase 3 pipeline), approve/
reject (checker-only, maker cannot approve own batch — enforced independent of role, not just
by role check), record versioning (`PUT /students/{apaar_id}/records/{id}`, material edits reset
to T1), trust tier T2 wired to APAAR registry match, hash-chained audit log (`SHA256(prev_hash ||
canonical_json(entry))`, written in the same transaction as the action it records, Postgres
advisory lock for ordering, DB trigger blocking direct UPDATE/DELETE on `audit_log`), Upload
Wizard / Approvals / Institution Dashboard / Audit Log UI pages. **269 tests pass.** Audit
tamper test: editing row 4 is reported at exactly index 3 (0-indexed) by `/audit/verify`;
deleting a row is caught at the row after it. Idempotency and maker-checker rules verified by
test and by an HTTP smoke run.

## Phase 5 — ML triage and Review Queue
ML triage engine implemented per spec sections 9, 11, 13, and 15:
- ML-1: Pairwise feature extraction, phonetic/lexical candidate blocking, calibrated LightGBM duplicate matcher, transfer-aware duplicate suppression (early exit when enrolment ended on/before admission date), priority routing (>=0.90 priority 1, 0.60–0.90 priority 2).
- ML-2: Institution anomaly detector using Isolation Forest trained on 9 peer-normalized metrics with peer z-score explanation vectors.
- ML-3: Shared-identity ring detector using bipartite graphs between records and contact tokens, Louvain community clustering across institutions.
- ML-4: Programme-name normaliser using character 2-4 gram TF-IDF vectorizer and cosine similarity against canonical catalog.
- AICTE Review Queue endpoints (`GET /api/v1/cases`, `GET /api/v1/cases/{id}`, `POST /api/v1/cases/{id}/decision`) with full audit logging and feedback loop (`ml_feedback` table and `retrain_with_feedback`).
- React Review Queue UI (`/aicte/review`) with case filters, comparative diff inspector, anomaly z-score graphs, and decision actions; Institution Cases UI (`/institution/cases`) with strict privacy isolation (AICTE_ONLY cases 404 for institutions).
**286 tests pass.** Full ML pipeline, F1 >= 0.85 floor, retrain loop, RBAC, transfer-awareness, and critical edge cases verified.

## Explicitly not started (Phases 6-9, per spec section 15)
- **Phase 6 — Dashboards and reports:** AICTE dashboard, SSE live updates, coverage meter, risk
  score, CSV reports.
- **Phase 7 — Student confirmation and verification:** T3 confirmation/dispute, consent flow,
  Ed25519 signed records + offline-verifiable QR, revocation, access log, certificate view,
  public `/verify` page.
- **Phase 8 — Demo mode and evaluation:** fraud-injection scenarios wired into live cases,
  concurrency-demo endpoint, tamper-audit helper, evaluation module/page.
- **Phase 9 — Hardening:** login rate limiting, security checklist, perf script, final full test
  run, README traceability table (PPT claim → requirement → code → test).

## Where to look
- `docs/architecture.md` — stack, repo layout, request flow, security posture so far.
- `docs/api.md` — full endpoint list, what's implemented vs. deferred.
- `docs/decisions.md` — every ambiguous-spec judgment call made (D1-D71), with rationale.
- `README.md` — setup, demo accounts, phase status table, known limitations.
- `APAAR_Ledger_Build_Spec.md` — the original authoritative spec driving all of the above.
