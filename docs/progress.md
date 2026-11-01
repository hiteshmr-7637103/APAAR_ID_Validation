# Progress summary — Phases 1-8 complete, Phase 9 not started

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

## Phase 6 — Dashboards and Reports
Dashboards and regulatory reports engine implemented per spec sections 5.6 (FR-21 to FR-25), 8, and 15:
- AICTE National Apex Dashboard (`GET /api/v1/dashboards/aicte`): National KPIs (institutions, students, intake vs enrolment, over-cap counts, excess seats), state breakdown, open cases summary, coverage distribution, trust tier split, risk leaderboard.
- Server-Sent Events (SSE) live updates stream (`GET /api/v1/dashboards/events/stream`): real-time auto-refresh on batch decisions and review case actions via `event_bus`.
- Headcount Coverage Meter (`GET /api/v1/dashboards/coverage` and `GET /api/v1/dashboards/institutions/{id}/coverage`): ratio of verified received student records to declared institutional headcount with configurable bands (RED < 50%, AMBER 50-89.9%, GREEN >= 90%).
- Data Health Score (`GET /api/v1/dashboards/institutions/{id}/health-score`): composite score 0-100 across completeness, validity, timeliness, and clean share with qualitative rating badges.
- Institution Risk Ranking (`GET /api/v1/dashboards/institutions/risk-ranking`): composite 4-factor risk score (`0.35 anomaly + 0.25 duplicate_rate + 0.20 ring_flags + 0.20 rule_violations`) with transparent breakdown and regulatory alert templates.
- Institution Dashboard: embedded coverage meter and Data Health Score (deferred block removed).
- 7 Regulatory & Statutory Reports (`GET /api/v1/reports` and `GET /api/v1/reports/{name}`): CSV exports and JSON previews with academic year and state filters (`enrolment-vs-intake`, `supernumerary-utilisation`, `over-cap-flags`, `cross-institution-duplicates`, `coverage`, `risk-ranking`, `trust-tiers`).
- React UI: `AicteDashboardPage` (`/aicte/dashboard`) with SSE live update indicator, `ReportsPage` (`/aicte/reports`) with preview and direct CSV downloads, updated `InstitutionDashboardPage` (`/institution/dashboard`).
**300 tests pass.** All 7 reports, aggregations, RBAC, coverage bands, health score, risk ranking, and SSE verified.

## Phase 7 — Student Confirmation and Verification
Student confirmation, selective-disclosure consent, asymmetric Ed25519 signing, and offline verification implemented per spec sections 5.7 (FR-26 to FR-34, FR-36), 8, 12, and 15:
- Student Enrolments & Confirmation (`GET /api/v1/students/me/enrolments`, `POST /api/v1/enrolments/{id}/confirm`): Confirmation elevates student record to T3 (highest trust tier) and publishes `trust_tier_updated` event.
- Dispute Enrolment (`POST /api/v1/enrolments/{id}/dispute`): "Not me" marks confirmation as DISPUTED, keeps tier unchanged, and opens a `STUDENT_DISPUTE` case (AICTE-only visibility, priority 1).
- Outbox Enrolment Confirmation Generator: batch approval automatically populates pending confirmations with 30-day expiration window.
- Consent Management (`POST /api/v1/verify/requests`, `GET /api/v1/consent/requests`, `POST /api/v1/consent/{id}/grant`, `POST /api/v1/consent/{id}/deny`, `POST /api/v1/consent/{id}/revoke`):
  - Verification requests by verified employers/institutions with purposeful scope.
  - Student granular selective disclosure (checkboxes for only chosen fields).
  - Consent revocation updates consent status, revokes signed credential hash into the revocation list, and blocks future access.
- Cryptographic Signing Engine (`app.services.signing`):
  - Active Ed25519 keypair generation and rotation storage with encrypted private keys.
  - Canonical JSON serialization (sorted keys, compact separators, UTF-8).
  - Compact QR payload generation and PNG rendering (`qrcode` + `Pillow`).
  - Dual-mode offline verifier (`POST /api/v1/verify/offline`): verifies both full selective-disclosure JSON bundles and compact QR payloads against public keys, expiry dates, and revocation registry.
- Student Verification Access Log (`GET /api/v1/students/me/access-log`): complete privacy-preserving log of verifiers, timestamps, purpose, and disclosed fields.
- Official Certificate View (`GET /api/v1/certificates/{record_id}`): printable verification certificate with APAAR ID right next to student ID, trust tier seal, and signed compact QR code.
- Public Endpoints (`/.well-known/public-key`, `/api/v1/revocations`, `/api/v1/verify/offline`).
- React UI:
  - `StudentPortal.tsx` (`/student/portal`): Enrolments with T3 Confirm & Dispute actions, pending Consent requests with selective disclosure checkboxes, and detailed Access log.
  - `VerifierPortal.tsx` (`/verifier/portal`): Submit verification requests, track decisions, and inspect verified records with live QR preview.
  - `PublicVerify.tsx` (`/verify`): Public offline Ed25519 verifier with sample valid payload, QR code scanner simulator, and tamper demonstration buttons.
  - `CertificateView.tsx` (`/certificates/:recordId`): Official university degree/enrolment certificate view.
**315 tests pass.** All 15 Phase 7 verification tests and all 300 prior phase tests pass.

## Phase 8 — Demo mode and evaluation
Demo mode and evaluation module implemented per spec sections 13, 14, and 15:
- Fraud Injection Scenarios (`GET /api/v1/demo/scenarios`, `POST /api/v1/demo/inject/{scenario}`):
  - 6 interactive scenarios: `cross_duplicate` (opens DUP_EXACT_CROSS review case), `genuine_transfer` (transfer-aware logic, INFO severity, 0 cases opened), `shared_phone_ring` (Louvain community detection opens RING case), `late_admission_burst` (Isolation Forest opens ANOMALY case with Z-score attributions), `seat_over_cap` (atomic ledger flags excess and opens SEAT_OVER_CAP case), and `headcount_only` (creates non-compliant institution with RED coverage band).
- Seat Ledger Concurrency Verification (`POST /api/v1/demo/concurrency-test`):
  - Contention stress test launching 50 parallel concurrent admissions on a test bucket with 5 seats remaining using `ThreadPoolExecutor` and `threading.Barrier`.
  - Verifies exact atomic enforcement: 5 clean admissions, 45 flagged over-cap, 0 lost updates, verified via PostgreSQL row-level locks.
- Cryptographic Audit Tamper Simulator (`POST /api/v1/demo/tamper-audit`):
  - Simulates direct database tampering on an append-only audit log entry.
  - Verifies cryptographic break detection at the exact altered index via `/audit/verify`.
  - Restores the original payload and confirms the cryptographic hash chain restores to 100% validity.
- Dynamic Quota Rules Editor (`GET /api/v1/rules`, `PUT /api/v1/rules/{key}`):
  - Rules-as-data editor with strict per-bucket schema validations and audit trail.
- Comprehensive Evaluation Module (`backend/app/eval/run_eval.py`, `GET /api/v1/evaluation`, `POST /api/v1/evaluation/run`):
  - Evaluates all 7 regulatory and architectural criteria per spec section 14:
    1. Duplicate Matcher: Precision, Recall, F1, PR-AUC, Brier score, ECE on both in-distribution and held-out corruptions.
    2. Deterministic Rules: Detection rate per injected fault vector (APAAR checksum, category mismatch, timeline age, future date).
    3. Anomaly & Ring Detectors: Top 10% ranking rate and Louvain cluster recall.
    4. Seat Ledger: Concurrency test invariants.
    5. Performance: p50/p95 latency and projected 10k-row throughput.
    6. Review-Time Saved Proxy: Automated clearance percentage and estimated human audit hours saved.
    7. Synthetic Benchmark Notice: Disclosing synthetic benchmark parameters with controlled fault injection.
  - Automatically writes benchmark report to `docs/evaluation.md`.
- React UI:
  - `DemoControlPage.tsx` (`/aicte/demo`): Interactive fraud injection cards, concurrency race runner, and audit tamper simulator.
  - `EvaluationPage.tsx` (`/aicte/evaluation`): Live benchmark dashboard across all 7 criteria with rerun triggers.
  - `RulesEditorPage.tsx` (`/aicte/rules`): Regulatory quota rules editor with instant JSON validation.
**328 tests pass.** All 13 Phase 8 demo/eval tests and all 315 prior phase tests pass.

## Explicitly not started (Phase 9, per spec section 15)
- **Phase 9 — Hardening:** login rate limiting, security checklist, perf script, final full test
  run, README traceability table (PPT claim → requirement → code → test).

## Where to look
- `docs/architecture.md` — stack, repo layout, request flow, security posture so far.
- `docs/api.md` — full endpoint list, what's implemented vs. deferred.
- `docs/decisions.md` — every ambiguous-spec judgment call made (D1-D71), with rationale.
- `README.md` — setup, demo accounts, phase status table, known limitations.
- `APAAR_Ledger_Build_Spec.md` — the original authoritative spec driving all of the above.
