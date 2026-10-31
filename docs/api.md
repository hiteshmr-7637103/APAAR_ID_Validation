# API surface (as built through Phase 4)

Base prefix: `/api/v1`. Auth: JWT bearer except `/health`, `/.well-known/public-key` (not yet
added — Phase 7), `/verify` (not yet added — Phase 7). Error shape:
`{ "error": {"code", "message_en", "message_key", "details"} }`.

## Auth
| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/auth/login` | any | OAuth2 password form; audited (incl. failures) |
| POST | `/auth/refresh` | any | refresh token only, not access |
| GET | `/auth/me` | any | |

## Institutions, programmes, users
| Method | Path | Role |
|---|---|---|
| GET | `/institutions` | scoped |
| GET | `/institutions/{id}` | scoped |
| GET | `/institutions/{id}/seats` | scoped (bucket/cap/used/excess) |
| GET | `/programmes` | any authenticated |
| GET | `/users` | AICTE_ADMIN |

## Records and uploads
| Method | Path | Role | Phase |
|---|---|---|---|
| POST | `/students` | INST_MAKER | 3 |
| GET | `/students/{apaar_id}` | scoped | 3 |
| PUT | `/students/{apaar_id}/records/{id}` | INST_MAKER | 4 (versioning, T1 reset) |
| POST | `/validate/record` | INST_MAKER | 3 (dry preview, no write) |
| GET | `/validate/codes` | any | 3 |
| GET | `/templates/students.csv` | INST_MAKER | 4 |
| POST | `/uploads` | INST_MAKER | 4 |
| PUT | `/uploads/{id}/chunks/{n}` | INST_MAKER | 4 |
| GET | `/uploads/{id}` | INST_MAKER | 4 (resume point) |
| POST | `/uploads/{id}/finalize` | INST_MAKER | 4 (idempotent) |
| POST | `/uploads/{id}/dry-run` | INST_MAKER | 4 (row report + projected bucket impact) |
| POST | `/uploads/{id}/submit` | INST_MAKER | 4 (idempotent) |
| GET | `/uploads/{id}/report` | INST_MAKER | 4 |
| GET | `/batches` / `/batches/{id}` / `/batches/{id}/records` | scoped | 4 |
| POST | `/batches/{id}/approve` | INST_CHECKER, not own batch | 4 |
| POST | `/batches/{id}/reject` | INST_CHECKER, not own batch | 4 (releases seats) |

## Cases, trust, rules
| Method | Path | Role | Phase |
|---|---|---|---|
| GET | `/cases` / `/cases/{id}` | scoped (AICTE_ONLY hidden from institutions) | 3 |
| POST | `/cases/{id}/decision` | AICTE (SEAT_OVER_CAP only so far) | 3 |
| GET | `/dashboards/trust-tiers` | scoped | 4 |
| GET | `/dashboards/institution/{id}` | scoped | 4 (buckets, tier split, cases) |
| GET | `/rules` / PUT `/rules/{key}` | AICTE_ADMIN | 3 (versioned, audited) |

## Audit and demo
| Method | Path | Role | Phase |
|---|---|---|---|
| GET | `/audit` | AICTE_ADMIN, AICTE_REVIEWER | 4 |
| GET | `/audit/verify` | AICTE_ADMIN, AICTE_REVIEWER | 4 (recomputes chain) |
| POST | `/demo/seed` | AICTE_ADMIN | 2-4 (generates + replays through rules/ledger) |
| GET | `/mock/apaar/{id}` | AICTE only (MOCK) | 2 |
| GET | `/health` | public | 1 |

## Not yet implemented (later phases per the spec)
- ML triage endpoints beyond the case types already handled; anomaly/ring case decisions.
- `GET /dashboards/aicte`, `/institutions/risk-ranking`, `/coverage`, `/events/stream` (SSE),
  `/reports/{name}` (Phase 6).
- `/verify/*`, `/consent/*`, `/certificates/{id}`, `/.well-known/public-key`, `/revocations`,
  `/students/me/enrolments`, `/enrolments/{id}/confirm|dispute`, `/students/me/access-log`
  (Phase 7).
- `/demo/inject/{scenario}`, `/demo/retrain`, `/demo/concurrency-test`, `/demo/tamper-audit`,
  `/evaluation`, `/evaluation/run` (Phase 8).
- Per-institution API keys for ERP integration, `scripts/sample_client.py` (Phase 9 / FR-39).
