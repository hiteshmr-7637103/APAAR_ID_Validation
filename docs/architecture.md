# Architecture (as built through Phase 4)

## Stack
- **Backend:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2.x (sync), Alembic migrations, PostgreSQL 16.
- **Auth:** OAuth2 password flow, JWT access (15 min) + refresh (7 days), bcrypt via passlib, role claim checked against the DB role (not the token) on every request.
- **Frontend:** React 18 + TypeScript + Vite + TailwindCSS + React Router.
- **Crypto:** Fernet (field-level encryption at rest for phone/email/address/sponsor/OCI number and upload chunk payloads), HMAC-SHA256 (privacy tokens for cross-institution matching), SHA-256 hash chain (audit log).
- **Packaging:** `docker-compose.yml` (db/api/web); API container runs Alembic migrations then seeds on first start.

## Repo layout
```
backend/app/
  api/        auth, institutions, students, uploads, validation, review, dashboards,
              audit, demo, mock_apaar, rules_admin, users
  core/       config, security, rbac, audit (hash chain), events (stub), crypto
  db/         models, session, seed
  services/   rule_engine, timeline, transfer, seat_ledger, trust, apaar_client,
              privacy_tokens, names, quota, cases, uploads, records, messages
  synth/      generator, corruptions, pools, loader, replay
  ml/         empty — reserved for Phase 5
  eval/       empty — reserved for Phase 8
frontend/src/ pages, components, api, hooks, i18n, lib
docs/         decisions.md (D1-D71), architecture.md, api.md, progress.md (this set)
```

## Request flow (student record, end to end so far)
```
Institution maker
  -> POST /students  or  Upload Wizard (chunked CSV -> dry-run -> submit)
  -> rule_engine (FR-6..FR-13, timeline/transfer checks)        [Phase 3]
  -> seat_ledger.admit() — SELECT...FOR UPDATE, flag not reject [Phase 3]
  -> trust tier T0 -> T1 (rules clean) -> T2 (APAAR match)       [Phase 3/4]
  -> status PENDING_APPROVAL, audit_log entry (same transaction) [Phase 4]
  -> INST_CHECKER approve/reject (not the same maker)            [Phase 4]
       approve -> ACTIVE
       reject  -> REJECTED, seat_ledger.release(), case excess updated
  -> student_record_versions row on every material edit, trust_tier reset to T1 [Phase 4]
```
ML triage, SSE dashboards, student confirmation (T3), consent/QR verification, and the
demo/evaluation modules are **not yet implemented** — see `docs/progress.md`.

## Data model
Full table list and indexes: spec section 7.1, implemented in
`backend/app/db/models.py` across Alembic migrations `0001`-`0004`. All tables from the
spec exist as of Phase 2, including ones not yet used by business logic (consent_requests,
verification_events, signing_keys, revocations — reserved for Phase 7).

## Security posture so far
- RBAC via `require_roles` dependency + `scope_to_institution`/`scope_cases` helpers — single
  choke point so institution users cannot see another institution's rows or `AICTE_ONLY` cases.
- Audit log is hash-chained (`SHA256(prev_hash || canonical_json(entry))`), written in the same
  DB transaction as the action, ordering serialized with a Postgres advisory lock, and a DB
  trigger blocks UPDATE/DELETE on `audit_log` directly. `GET /audit/verify` recomputes the chain.
- Cross-institution duplicate/timeline logic never leaks another institution's data to an
  institution user — only a neutral INFO message. Matching uses HMAC tokens, not raw APAAR IDs,
  for case keys.
- Known limitation (recorded in README): the audit chain isn't anchored outside the DB, so a
  DB-owner-level attacker could rewrite the chain from a tampered row onward undetected. Fine for
  a hackathon prototype; would need external anchoring (e.g. periodic external notarization) for
  production.

## What's mocked
- APAAR registry (`services/apaar_client.py`, `/mock/apaar/{id}`) — synthetic records only.
- Authority/ABC/DigiLocker, Aadhaar/e-KYC — not implemented (reserved interfaces only, Phase 7+).
- Email/SMS — written to a `notifications` table, shown in UI, never actually sent.

See `docs/decisions.md` for the full log of judgment calls made while filling in spec ambiguities
(D1 through D71 so far), and the project's `APAAR_Ledger_Build_Spec.md` for the authoritative spec
this build follows.
