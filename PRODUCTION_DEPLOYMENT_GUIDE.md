# Mugogo Engine — Production Deployment & Verification Guide

> **Version:** 2.1 — Post Sprint 4 (Onboarding + Reporting)
> **Last Updated:** 2026-10-06
> **Stack:** FastAPI · PostgreSQL · SQLAlchemy 2.0 · Alembic · Next.js 14

---

## Part 1: Production Environment Configuration

All variables below match exactly those read by [`app/core/config.py`](app/core/config.py). Variable names are **case-insensitive** (pydantic-settings normalises them). Copy `.env.example` as your starting point.

### 1.1 Required Variables

| Variable | Description | Example |
|---|---|---|
| `APP_ENV` | Runtime environment label | `production` |
| `APP_SECRET_KEY` | JWT signing secret (min 64 chars, random) | `openssl rand -hex 32` |
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://user:pass@host:5432/mugogo_prod` |
| `WEBHOOK_SIGNING_SECRET` | HMAC secret for inbound webhook verification | `openssl rand -hex 32` |
| `CLOUDFLARE_WORKER_SECRET` | Auth header value checked by ingest workers | any strong secret |
| `ENCRYPTION_KEY` | Fernet AES key for OAuth token encryption | see generation command below |
| `BLOG_SERVICE_TOKEN` | Internal auth token for blog create/publish | any strong secret |
| `NEXTJS_REVALIDATE_URL` | Next.js ISR revalidation webhook URL | `https://engine.mugogoinc.com/api/revalidate` |
| `NEXTJS_REVALIDATE_SECRET` | Secret checked by the revalidation route | any strong secret |
| `CORS_ORIGINS` | Comma-separated list of allowed origins | `https://engine.mugogoinc.com,https://www.biztali.com` |

### 1.2 Google Ads Variables

| Variable | Description | Required? |
|---|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Your approved Google Ads API developer token | ✅ Yes |
| `GOOGLE_ADS_CLIENT_ID` | OAuth2 client ID (Google Cloud project) | ✅ Yes |
| `GOOGLE_ADS_CLIENT_SECRET` | OAuth2 client secret | ✅ Yes |
| `GOOGLE_ADS_REFRESH_TOKEN` | Long-lived refresh token from OAuth flow | ✅ Yes |
| `GOOGLE_ADS_CUSTOMER_ID` | Default client customer ID (no dashes) | ✅ Yes |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | MCC Manager Account ID (if using MCC) | Optional |
| `GOOGLE_ADS_CONVERSION_ACTION_ID` | Conversion action for offline uploads | Optional |
| `CPL_THRESHOLD_USD` | Cost-per-lead alert threshold in USD | Default: `15.0` |

### 1.3 WhatsApp Cloud API Variables

| Variable | Description |
|---|---|
| `WA_PHONE_NUMBER_ID` | Meta WhatsApp Cloud API phone number ID |
| `WA_ACCESS_TOKEN` | Permanent system user access token from Meta Business |
| `WA_API_VERSION` | Graph API version (default: `v20.0`) |

### 1.4 Optional Variables

| Variable | Description |
|---|---|
| `META_PIXEL_ID` | Meta Pixel ID for CAPI integration |
| `META_CAPI_ACCESS_TOKEN` | Meta Conversions API access token |
| `RESEND_API_KEY` | Resend transactional email API key |
| `EMAIL_FROM_ADDRESS` | Sender address (default: `noreply@mugogoinc.com`) |

### 1.5 Key Generation Commands

```bash
# Generate APP_SECRET_KEY
openssl rand -hex 32

# Generate ENCRYPTION_KEY (Fernet — required for OAuth token storage)
source .venv/bin/activate
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate WEBHOOK_SIGNING_SECRET
openssl rand -hex 32
```

> [!CAUTION]
> Never commit real keys to `.env`. Use your CI/CD secret manager (GitHub Secrets, Railway Variables, etc.) to inject them at deploy time. The `.env.example` file in the repo is safe to commit — it contains no real values.

---

## Part 2: Database Migration Checklist

The production database **must** be on migration head `006` before starting the API.

### 2.1 Alembic Migration History

```
006 (head) — add_agent_org_assignments_and_client_marketing_channels
005         — add_is_partial_to_leads
004         — add_returning_lead_tracking_fields
003         — add_is_frozen_to_users
002         — (previous)
001         — initial_postgres_schema
```

### 2.2 Migration Commands

```bash
cd mugogoinc_api

# 1. Activate virtualenv
source .venv/bin/activate

# 2. Check current revision on production DB
PYTHONPATH=$(pwd) alembic current
# Expected output: 006 (head)

# 3. If not at head, run:
PYTHONPATH=$(pwd) alembic upgrade head

# 4. Verify
PYTHONPATH=$(pwd) alembic current
```

### 2.3 Direct PostgreSQL Table Verification

After migrating, connect to your production DB and confirm the Sprint 2 tables exist:

```sql
-- Confirm new tables
\dt agent_org_assignments
\dt client_marketing_channels

-- Confirm unique constraint (prevents duplicate agent-org pairings)
\d agent_org_assignments
-- Look for: "uq_agent_org_assignment" UNIQUE (agent_id, org_id)

-- Confirm JSON column exists
\d client_marketing_channels
-- Look for: gsc_platform_properties jsonb
--           encrypted_oauth_tokens   jsonb
```

---

## Part 3: Feature Verification Checklist (Production)

Run these checks in priority order after deploying. All endpoints require a valid JWT `Authorization: Bearer <token>` header unless noted.

---

### ✅ Priority 1 — Security & RBAC Isolation (Sprint 1)

**Goal:** Confirm no cross-tenant PII leaks between agents.

#### Step 1 — Unapproved Agent sees blurred PII
```bash
# 1. Register a new agent account
POST /api/v1/auth/register
{ "name": "Test Agent", "email": "agent@test.com",
  "phone_number": "+254700000001", "password": "TestPass123" }

# 2. Login as that agent
POST /api/v1/auth/login
{ "email": "agent@test.com", "password": "TestPass123" }
# → Save access_token

# 3. Query leads as unapproved agent
GET /api/v1/leads/
Authorization: Bearer <agent_token>

# ✅ Expected: 200 OK, all leads returned with:
#    full_name    = "J*** D***"
#    phone_number = "+254***0001"
#    email        = "j***@***.com"
#    is_blurred   = true
```

#### Step 2 — Approved agent assigned to org sees real PII
```bash
# As Super Admin:
PATCH /api/v1/users/<agent_id>/approve

# Assign agent to org (via AgentOrgAssignment — Sprint 2 table):
# Insert directly in DB or via your admin tool:
INSERT INTO agent_org_assignments (id, agent_id, org_id)
VALUES (gen_random_uuid(), '<agent_id>', '<org_id>');

# Re-query leads as approved agent
GET /api/v1/leads/?organization_id=<org_id>
Authorization: Bearer <agent_token>

# ✅ Expected: Leads for that org returned UNBLURRED
# ✅ Expected: Leads for OTHER orgs still blurred or absent
```

---

### ✅ Priority 2 — Database Architecture (Sprint 2)

**Goal:** Confirm `agent_org_assignments` and `client_marketing_channels` persist correctly.

```sql
-- After running onboarding (Priority 3), verify channel record was saved:
SELECT org_id, website_url, gsc_platform_properties
FROM client_marketing_channels
ORDER BY created_at DESC
LIMIT 5;

-- Expected: gsc_platform_properties contains discovered socials + google_ads_customer_id:
-- { "instagram.com": "https://...", "google_ads_customer_id": "123-456-7890" }

-- Verify duplicate agent-org assignment is rejected:
INSERT INTO agent_org_assignments (id, agent_id, org_id)
VALUES (gen_random_uuid(), '<same_agent_id>', '<same_org_id>');
-- ✅ Expected: ERROR — duplicate key violates unique constraint "uq_agent_org_assignment"
```

---

### ✅ Priority 3 — Onboarding: Scraper + Keyword Engine (Sprint 3)

**Goal:** Confirm the full context-builder pipeline works end-to-end.

```bash
POST /api/v1/onboarding/analyze-entity
Authorization: Bearer <manager_or_admin_token>
Content-Type: application/json

{
  "org_id": "<valid_org_uuid>",
  "website_url": "https://example.co.ke",
  "customer_id": "123-456-7890"
}
```

**Expected Response Structure:**
```json
{
  "status": "success",
  "data": {
    "website_analysis": {
      "success": true,
      "title": "...",
      "meta_description": "...",
      "headings": ["...", "..."]
    },
    "seed_keywords_used": ["...", "..."],
    "keyword_opportunities_count": 10,
    "top_keywords": [
      {
        "keyword": "safari packages kenya",
        "monthly_search_volume": 12000,
        "competition": "HIGH",
        "low_top_of_page_bid_usd": 0.45,
        "high_top_of_page_bid_usd": 2.10
      }
    ],
    "social_insights": { "discovered_socials": { "instagram.com": "https://..." } }
  }
}
```

> [!NOTE]
> If `keyword_opportunities_count` is 0 but scraping succeeded, your Google Ads credentials (`GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_CUSTOMER_ID`) are likely missing or incorrect. Check the API logs for `[Google Ads]` error lines.

---

### ✅ Priority 4 — Social Discovery Layer

**Goal:** Confirm Google-indexed social profiles are discovered and persisted.

```bash
# After the onboarding call above, inspect the DB record:
SELECT gsc_platform_properties
FROM client_marketing_channels
WHERE org_id = '<org_uuid>';

# ✅ Expected: JSON column populated with any discovered platforms:
# {
#   "instagram.com": "https://www.instagram.com/clientbrand/",
#   "youtube.com":   "https://www.youtube.com/@clientbrand",
#   "google_ads_customer_id": "123-456-7890"
# }
```

> [!NOTE]
> The social discovery uses Google's public index. Results depend on how well the brand is indexed. Newly created or low-traffic sites may return 0 results — this is expected behaviour, not a bug.

---

### ✅ Priority 5 — Client Onboarding Frontend (`/onboarding`)

**Goal:** Confirm the Next.js page renders correctly and connects to the API.

1. Open `https://engine.mugogoinc.com/onboarding` in production.
2. Log in as a Manager or Super Admin.
3. Input a valid **Organization ID** (copy from `/organizations` page) and a **website URL**.
4. Click **"Run Full Analysis"**.

**Expected UI Behaviour:**
- Loading shimmer appears while the backend processes (~2–5s).
- **Website Analysis card** shows: page title, meta description, headings chips, seed keywords.
- **Social Footprint card** shows: coloured platform cards (Instagram, TikTok, X, YouTube) — each is a clickable external link.
- **Keyword Opportunities table** shows: keyword phrase, monthly volume, competition badge (LOW/MEDIUM/HIGH in colour), bid range.
- "Client Onboarding" appears highlighted in the left **Sidebar** nav.

> [!IMPORTANT]
> The `NEXT_PUBLIC_API_URL` environment variable in the frontend `.env.production` must point to the correct API base URL:
> ```
> NEXT_PUBLIC_API_URL=https://api.mugogoinc.com/api/v1
> ```

---

### ✅ Priority 6 — WhatsApp Automated Reporting

**Goal:** Confirm weekly report generation and dispatch works.

#### Step 1 — Preview (no dispatch)
```bash
GET /api/v1/reports/weekly-preview?org_id=<org_uuid>
Authorization: Bearer <manager_or_admin_token>

# ✅ Expected: 200 OK with full report text:
# {
#   "status": "preview",
#   "org_name": "...",
#   "summary": { "total_leads": 14, "delivered": 12, ... },
#   "message": "📊 *Mugogo Engine — Weekly Report*\n..."
# }
```

#### Step 2 — Live Dispatch
```bash
POST /api/v1/reports/weekly-dispatch
Authorization: Bearer <manager_or_admin_token>
Content-Type: application/json

{
  "recipient_phone": "+254700000000",
  "org_id": "<org_uuid>"
}

# ✅ Expected: { "status": "dispatched", ... }
# ✅ Expected: WhatsApp message received within 5–10 seconds
```

#### Step 3 — Verify in Meta Dashboard
1. Open [Meta Business Suite](https://business.facebook.com) → WhatsApp Manager.
2. Check **Message Logs** to confirm the outbound message was accepted (`200 OK`).

> [!WARNING]
> If `WA_PHONE_NUMBER_ID` or `WA_ACCESS_TOKEN` are not set, the system falls back to **stub mode** — it logs the message but does NOT dispatch. Check API logs for `[WA STUB]` lines to confirm.

---

## Part 4: CI/CD & Deployment Notes

### 4.1 GitHub Actions Workflow
The backend deploy workflow is at [`.github/workflows/deploy-api.yml`](.github/workflows/deploy-api.yml). Before merging to `main`:

1. Ensure all GitHub Secrets are set (matching the env variables in Part 1).
2. Confirm `alembic upgrade head` runs in the deploy step **before** the API process starts.
3. The health check endpoint at `GET /` should return `{ "status": "operational" }` after deploy.

### 4.2 Startup Verification Commands
```bash
# Check all 25 tests pass before any deploy
cd mugogoinc_api
source .venv/bin/activate
python -m pytest tests/ -q
# ✅ Expected: 25 passed

# Verify app loads without import errors
python -c "from app.main import app; print('✅ App loaded OK')"

# Confirm migration head
PYTHONPATH=$(pwd) alembic current
# ✅ Expected: 006 (head)
```

### 4.3 Known Non-Critical Warnings
These warnings appear in tests and are from third-party libraries — **not your code**. No action needed until upstream packages release fixes:

| Warning | Source | Action |
|---|---|---|
| `crypt` deprecated | `passlib` (Python 3.12) | Migrate to `passlib[bcrypt]` before Python 3.13 upgrade |
| Class-based `config` deprecated | `pydantic` v2.x | Migrate to `ConfigDict` before Pydantic v3 release |

---

## Part 5: Quick API Reference

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/v1/auth/login` | POST | — | Obtain JWT access token |
| `/api/v1/auth/me` | GET | JWT | Current user info |
| `/api/v1/leads/` | GET | JWT | List leads (RBAC scoped) |
| `/api/v1/organizations` | GET | JWT | List organizations |
| `/api/v1/onboarding/analyze-entity` | POST | JWT (Manager+) | Run full client analysis |
| `/api/v1/reports/weekly-preview` | GET | JWT (Manager+) | Preview weekly report |
| `/api/v1/reports/weekly-dispatch` | POST | JWT (Manager+) | Dispatch report via WhatsApp |
| `/api/v1/marketing/keywords/search` | POST | JWT | Google Ads keyword ideas |
| `/api/v1/marketing/campaigns/check-health` | POST | JWT | Budget burn & CPL audit |
| `/api/v1/users/<id>/approve` | PATCH | JWT (Admin) | Approve agent |
| `/api/v1/users/<id>/freeze` | PATCH | JWT (Admin) | Freeze agent |
| `/docs` | GET | — | Swagger UI (all endpoints) |
| `/redoc` | GET | — | ReDoc API docs |
