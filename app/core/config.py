from __future__ import annotations

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: str = "development"
    app_secret_key: str = "insecure-default-key"

    database_url: str = "postgresql+asyncpg://mugogo_user:YOUR_PASSWORD@localhost:5432/mugogo_db"

    # ── WhatsApp Cloud API ───────────────────────────────────────────────────
    wa_phone_number_id: str = ""
    wa_access_token: str = ""
    wa_api_version: str = "v20.0"

    # ── Resend Email ─────────────────────────────────────────────────────────
    resend_api_key: str = ""
    email_from_address: str = "noreply@mugogoinc.com"

    # ── HMAC Signing & Worker Secrets ─────────────────────────────────────────
    webhook_signing_secret: str = "insecure-signing-secret"
    cloudflare_worker_secret: str = "dummy-cloudflare-worker-key-2026"

    # ── Token Encryption (Fernet AES-128-CBC) ────────────────────────────────
    # Generate a production key with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = "bm90LWEtcmVhbC1rZXktcGxlYXNlLWNoYW5nZS1tZS0hISE="

    # ── Subdirectory Blog & Revalidation ─────────────────────────────────────
    blog_service_token: str = "mugogo-blog-internal-secret-token"
    nextjs_revalidate_url: str = "http://localhost:3000/api/revalidate"
    nextjs_revalidate_secret: str = "revalidate-secret-token"

    # ── Meta CAPI & Marketing APIs ───────────────────────────────────────────
    meta_pixel_id: str = ""
    meta_capi_access_token: str = ""
    google_ads_developer_token: str = ""
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_customer_id: str = ""
    google_ads_login_customer_id: str | None = None
    google_ads_conversion_action_id: str = ""
    cpl_threshold_usd: float = 15.0

    # ── Google Search Console OAuth ──────────────────────────────────
    # Falls back to the shared Google Ads credentials if not separately specified.
    gsc_client_id: str = ""
    gsc_client_secret: str = ""

    cors_origins: str = "https://engine.mugogoinc.com,http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:5173,http://localhost:8080,http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:3002,http://127.0.0.1:5173,http://127.0.0.1:8080"

    @property
    def allowed_origins(self) -> List[str]:
        origins = [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one allowed origin")
        return origins

    @property
    def wa_api_url(self) -> str:
        return (
            f"https://graph.facebook.com/{self.wa_api_version}"
            f"/{self.wa_phone_number_id}/messages"
        )

    @property
    def sync_database_url(self) -> str:
        """Return synchronous database URL for Alembic migrations."""
        url = self.database_url
        if url.startswith("postgresql+asyncpg://"):
            return url.replace("postgresql+asyncpg://", "postgresql://")
        return url


settings = Settings()
