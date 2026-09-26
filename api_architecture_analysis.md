# API Architecture Analysis

## Summary

### ✅ What's Built & Working

- FastAPI app + CORS middleware: ✅ Solid
- Async SQLAlchemy + aiosqlite: ✅ Correct
- 4 entity models (User, Org, BusinessUnit, Lead): ✅ Well-structured
- Lead ingestion with deduplication: ✅ Works
- WhatsApp / Webhook / Email services: ✅ Stub-safe
- Dashboard KPI stats: ✅ Functional

### 🔴 Critical Gaps (Need Fixing First)

1. Zero authentication — Every single route (`/organizations`, `/leads`, `/stats`, etc.) is completely open. Anyone with the URL can read, create, or delete data.
2. `User` model has no password field — The `users` table exists but can't authenticate anyone; no `hashed_password` column.
3. No auth router — No `POST /auth/login`, `POST /auth/register`, or `GET /auth/me` endpoints exist at all.
4. `User` table never created on startup — `database.py` imports `organization`, `business_unit`, and `lead` but skips `user`, so the users table is never initialized.
5. `APP_SECRET_KEY` is declared but never used — The JWT signing key exists in config but nothing consumes it.

### 🟡 High-Priority Gaps

- Phone number validation — The `phonenumbers` library is in `requirements.txt` and the schema comments say "E.164 format" but no actual validator runs.
	- Status: fixed — `LeadIngestRequest` now validates and normalizes phone numbers to E.164 using `phonenumbers`.
- No Alembic migrations — `alembic` is installed but no `alembic/` directory exists; the app uses `create_all` which is dev-only.
- SQLite in production — Not safe for concurrent writes; PostgreSQL config is commented out.
- No rate limiting — The ingest endpoint especially is open to spam/flooding.
	- Status: mitigated — added a simple in-memory rate limiter for `POST /ingest/{business_unit_id}` (dev-only).
- 15 total gaps documented in the artifact above, each with severity and recommended fix.


---

Analysis generated on request. Next recommended steps:

- Add JWT-based auth, create `auth` router, and ensure `User` model stores `hashed_password`.
- Initialize `users` table on startup and add an admin seed user.
- Add phone validation and rate limiting on ingest.
- Replace SQLite with PostgreSQL for production or at least document migration steps and add Alembic.

**Production Readiness — Route & API Checklist**

- **Auth & Authorization:** Partially implemented — `/auth/register`, `/auth/login`, `/auth/me` exist, but most route handlers (`/organizations`, `/leads`, `/stats`, `/ingest`) are still unprotected. Add role-based access control and apply token dependency to sensitive endpoints.
- **Input Validation:** Good coverage with Pydantic models; phone validation added. Ensure strict size limits on `payload_meta` and sanitize nested values.
- **Rate Limiting:** Dev in-memory limiter added for `ingest`. Replace with Redis-backed limiter (e.g., `fastapi-limiter`) in production.
- **Migrations:** No Alembic migrations present. Scaffold `alembic/` and add initial migration; switch away from `create_all` in production.
- **Database:** SQLite (aiosqlite) is fine for development but not for concurrent production workloads. Provide PostgreSQL DATABASE_URL and test connection pooling.
- **Logging & Monitoring:** Basic logging exists. Add structured logs, request tracing, and health/metrics endpoints (`/metrics`) for Prometheus.
- **Secrets & Config:** `APP_SECRET_KEY` exists in config but rotate and store in a secrets manager (Vault/ENV). Enforce non-default secrets in CI/CD.
- **Error Handling:** Add global exception handlers to avoid leaking stack traces and to standardize error payloads.
- **Background Tasks & Workers:** Current `BackgroundTasks` approach is fine for lightweight async work; for heavier or long-running tasks use a queue (Redis/RabbitMQ + Celery/RQ) and retry policies.
- **Testing:** Add integration tests covering auth flow (register/login/me), ingest dedupe, and rate limiting behavior. Use a test DB and fixtures.
- **Security Hardening:** Enforce HTTPS, CORS origins locked to trusted domains, add CSP headers, and enable security headers middleware.

I generated a Postman collection you can import to test the main routes locally and exercise the auth flow.



