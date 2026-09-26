from __future__ import annotations

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def hmac_sign(payload: str) -> str:
    """Generate HMAC-SHA256 signature for outbound enterprise webhooks.

    The resulting hex digest is sent as the ``X-Mugogo-Signature`` header
    so downstream CRMs can verify payload authenticity.
    """
    return hmac.new(
        settings.webhook_signing_secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


async def sha256_hex(value: str) -> str:
    """Return lowercase hex SHA-256 digest of *value*."""
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(plain_password: str) -> str:
    """Hash a password for storage."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=1))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.app_secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])  # raises on invalid
