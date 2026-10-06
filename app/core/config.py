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

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./mugogo_engine.db"

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

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: str = "https://engine.mugogoinc.com,http://localhost:3000,http://localhost:3002"

    @property
    def allowed_origins(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

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
        elif url.startswith("sqlite+aiosqlite://"):
            return url.replace("sqlite+aiosqlite://", "sqlite://")
        return url


settings = Settings()

