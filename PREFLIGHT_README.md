# 🚀 Mugogo Engine — Developer Production Pre-Flight README

> **Status:** READY FOR PRODUCTION PUSH 🚀
> This document is the final operational checklist for deploying and verifying
> the Mugogo digital marketing Agent OS in production.

---

## 1. 🔑 Final GitHub Secrets Checklist

Ensure the following secrets are fully populated under your GitHub repository
(**`Settings → Secrets and variables → Actions`**). **Never store real key values
in this file or any file tracked by Git.**

### Backend (`deploy-api.yml`)

| Secret Name | Description | Required? |
|---|---|---|
| `SSH_HOST` | Production server IP or hostname | ✅ Required |
| `SSH_USER` | SSH username on the server | ✅ Required |
| `SSH_KEY` | Private SSH key (PEM format, entire content) | ✅ Required |
| `APP_SECRET_KEY` | JWT signing secret — generate: `openssl rand -hex 32` | ✅ Required |
| `DATABASE_URL` | PostgreSQL async URL: `postgresql+asyncpg://user:pass@host:5432/mugogo_prod` | ✅ Required |
| `WEBHOOK_SIGNING_SECRET` | HMAC secret for inbound webhook verification | ✅ Required |
| `CLOUDFLARE_WORKER_SECRET` | Auth header value checked by ingest edge workers | ✅ Required |
| `ENCRYPTION_KEY` | Fernet AES key for OAuth token encryption — generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | ✅ Required |
| `BLOG_SERVICE_TOKEN` | Internal auth token for blog create/publish endpoints | ✅ Required |
| `NEXTJS_REVALIDATE_URL` | Next.js ISR revalidation webhook URL | ✅ Required |
| `NEXTJS_REVALIDATE_SECRET` | Secret checked by the revalidation route | ✅ Required |
| `CORS_ORIGINS` | Comma-separated allowed origins: `https://engine.mugogoinc.com,https://www.biztali.com` | ✅ Required |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Approved Google Ads API developer token | ✅ Required |
| `GOOGLE_ADS_CLIENT_ID` | OAuth2 client ID from Google Cloud project | ✅ Required |
| `GOOGLE_ADS_CLIENT_SECRET` | OAuth2 client secret | ✅ Required |
| `GOOGLE_ADS_REFRESH_TOKEN` | Long-lived MCC refresh token | ✅ Required |
| `GOOGLE_ADS_CUSTOMER_ID` | Default client account ID (no dashes) | ✅ Required |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | MCC Manager Account ID (if using MCC) | Optional |
| `GOOGLE_ADS_CONVERSION_ACTION_ID` | Conversion action for offline uploads | Optional |
| `CPL_THRESHOLD_USD` | Cost-per-lead alert threshold (default `15.0`) | Optional |
| `WA_PHONE_NUMBER_ID` | Meta WhatsApp Cloud API phone number ID | Optional* |
| `WA_ACCESS_TOKEN` | Permanent Meta system user access token | Optional* |
| `META_PIXEL_ID` | Meta Pixel ID for CAPI integration | Optional |
| `META_CAPI_ACCESS_TOKEN` | Meta Conversions API access token | Optional |
| `RESEND_API_KEY` | Resend transactional email API key | Optional |

> [!NOTE]
> *Optional means the app boots without it, but that feature stubs silently.
> If `WA_ACCESS_TOKEN` is blank, WhatsApp dispatch falls back to log-only mode.
> Watch for `[WA STUB]` lines in PM2 logs to confirm.

### Frontend (`deploy-frontend.yml`)

| Secret Name | Description | Required? |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | Production API base URL baked into the Next.js bundle at build time | ✅ Required |

> [!CAUTION]
> `NEXT_PUBLIC_*` variables are embedded in the JavaScript bundle and become
> **publicly visible** to any browser. Never put secrets (tokens, passwords) in
> `NEXT_PUBLIC_*` variables — only public-safe URLs go here.

---

## 2. ⚙️ Automated Deployment Pipeline Guarantees

Your CI/CD workflows in [`.github/workflows/`](.github/workflows/) handle the
following automatically on every push to `main`:

| Guarantee | Implementation |
|---|---|
| **Zero manual migrations** | `PYTHONPATH=. alembic upgrade head` runs before `pm2 restart` |
| **Aborts on migration failure** | `set -euo pipefail` — any failure exits the deploy immediately; old process keeps serving |
| **Complete .env on every deploy** | All secrets injected via `env:` + `envs:` — never interpolated into script body |
| **No secret leakage in logs** | Secrets passed as shell env vars, not string interpolation — not visible in Actions log |
| **PM2 environment refresh** | `pm2 restart --update-env` forces PM2 to re-read `.env` on every deploy |
| **Post-deploy health check** | Polls `GET /` up to 3×; exits `1` (failing the Action) if API doesn't respond `200` |

---

## 3. 🔍 Post-Deployment Verification Checklist

Once GitHub Actions reports ✅ success, run this sequence to confirm all features
are live and functional.

---

### ✅ Step 1 — API Health & Migration State

```bash
# 1. Verify the API root responds
curl -s https://api.mugogoinc.com/ | jq .
# ✅ Expected: { "status": "operational", "version": "1.1.0" }

# 2. SSH into server to confirm migration revision
cd /var/www/mugogoinc/mugogoinc_api
source venv/bin/activate
PYTHONPATH=. alembic current
# ✅ Expected: 006 (head)
```

---

### ✅ Step 2 — RBAC & Lead Privacy

```bash
# Register and login as a plain (unapproved) agent, then query leads:
curl -s https://api.mugogoinc.com/api/v1/leads/ \
  -H "Authorization: Bearer <unapproved_agent_token>" | jq '.items[0].full_name'
# ✅ Expected: "J*** D***"  (blurred PII)
```

---

### ✅ Step 3 — Onboarding Engine (Scraper + Keywords + Social)

```bash
curl -s -X POST https://api.mugogoinc.com/api/v1/onboarding/analyze-entity \
  -H "Authorization: Bearer <manager_or_admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "org_id": "<valid_org_uuid>",
    "website_url": "https://example.co.ke"
  }' | jq '{
    title:          .data.website_analysis.title,
    keywords_found: .data.keyword_opportunities_count,
    socials_found:  (.data.social_insights.discovered_socials | keys)
  }'

# ✅ Expected:
# {
#   "title":          "Example — Home",
#   "keywords_found": 10,
#   "socials_found":  ["instagram.com", "youtube.com"]
# }
```

---

### ✅ Step 4 — Frontend Onboarding Dashboard

1. Navigate to `https://engine.mugogoinc.com/onboarding`.
2. Log in as Manager or Super Admin.
3. Enter a valid **Organization ID** and **website URL**.
4. Click **"Run Full Analysis"**.

**Expected UI:**
- Loading shimmer while backend processes (~2–5 s).
- **Website Analysis card** — page title, meta description, heading chips, seed keywords.
- **Social Footprint card** — coloured platform cards (Instagram, TikTok, X, YouTube) as clickable external links.
- **Keyword Opportunities table** — keyword, monthly volume, competition badge (LOW/MEDIUM/HIGH), bid range.
- **Sidebar** shows "Client Onboarding" as an active nav item.

---

### ✅ Step 5 — WhatsApp Weekly Report

```bash
# Preview first (no dispatch):
curl -s "https://api.mugogoinc.com/api/v1/reports/weekly-preview?org_id=<org_uuid>" \
  -H "Authorization: Bearer <manager_or_admin_token>" | jq '.summary'

# Then dispatch live:
curl -s -X POST https://api.mugogoinc.com/api/v1/reports/weekly-dispatch \
  -H "Authorization: Bearer <manager_or_admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "recipient_phone": "+254700000000",
    "org_id": "<valid_org_uuid>"
  }' | jq .status

# ✅ Expected: "dispatched"
# ✅ Expected: WhatsApp message received on phone within 5–10 seconds
# ✅ Verify in Meta Business Suite → WhatsApp Manager → Message Logs
```

---

## 4. 🛑 Rollback & Emergency Protocol

### If the API fails to start after deploy:

```bash
# Check live PM2 logs
pm2 logs mugogo-api --lines 50

# Common causes:
# [WA STUB]     → WhatsApp env vars missing (non-fatal, app still runs)
# ImportError   → requirements.txt out of sync; re-run pip install
# alembic error → Migration failed; see below
```

### If a database migration fails:

```bash
# Roll back one revision
cd /var/www/mugogoinc/mugogoinc_api
source venv/bin/activate
PYTHONPATH=. alembic downgrade -1

# Confirm the revision you rolled back to
PYTHONPATH=. alembic current

# Fix the migration file, commit, and re-trigger the GitHub Action
```

### If PM2 is serving stale environment (keys not updated):

```bash
pm2 restart mugogo-api --update-env
pm2 save
```

---

## 5. 📋 Complete Feature → Test Mapping

| Feature | Sprint | Verification Step | Tests |
|---|---|---|---|
| Lead RBAC & PII blurring | Sprint 1 | Step 2 | `test_lead_privacy_rbac.py` |
| Fernet token encryption | Sprint 1 | Check `encrypted_oauth_tokens` in DB | `test_security.py` |
| `agent_org_assignments` table | Sprint 2 | `alembic current` → `006` | Schema migration |
| `client_marketing_channels` table | Sprint 2 | DB query after onboarding | Schema migration |
| Website scraper | Sprint 3 | Step 3 (`title`, `headings`) | `test_onboarding.py` |
| Google Ads keywords | Sprint 3 | Step 3 (`keyword_opportunities_count`) | `test_marketing.py` |
| Social discovery | Sprint 3+ | Step 3 (`socials_found`) | Integrated |
| Onboarding frontend `/onboarding` | Path 1 | Step 4 | UI verification |
| WhatsApp weekly report | Path 2 | Step 5 | `test_reporting.py` |

---

## 6. 🔐 Security Reminders

> [!CAUTION]
> - **Never commit real secret values** to any file tracked by Git — including this README, `.env.example`, or comments in code.
> - **Rotate the `ENCRYPTION_KEY` immediately** if it was ever written in plaintext anywhere (chat, docs, email). All tokens encrypted with the old key must be re-encrypted.
> - **Rotate `APP_SECRET_KEY`** if compromised — this invalidates all existing user JWTs (all users must re-login).
> - `ENCRYPTION_KEY` must be a valid Fernet base64 key. Generate a fresh one with:
>   ```bash
>   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
>   ```
