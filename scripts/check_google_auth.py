"""Diagnose Google OAuth credentials without printing any secret values.

Checks:
1. Google Ads refresh token (global ``GOOGLE_ADS_*`` credentials).
2. Google Ads API access (developer token + customer visibility).
3. GSC client credentials against a per-Business-Unit stored refresh token.

Run on the server (or locally with a valid .env):

    PYTHONPATH=. python scripts/check_google_auth.py

Exit code is always 0 unless ``--strict`` is passed, so it is safe to run as
a non-fatal deploy diagnostic.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.business_unit import BusinessUnit

TOKEN_URL = "https://oauth2.googleapis.com/token"


async def _check_refresh(
    label: str,
    client_id: str,
    client_secret: str,
    refresh_token: str | None,
) -> bool:
    if not refresh_token:
        print(f"[{label}] MISSING refresh token")
        return False
    if not client_id or not client_secret:
        print(f"[{label}] MISSING client id/secret")
        return False

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if resp.status_code == 200:
        print(f"[{label}] OK — refresh token is valid")
        return True

    try:
        payload = resp.json()
        error = payload.get("error", resp.text[:200])
        description = payload.get("error_description", "")
    except Exception:
        error = resp.text[:200]
        description = ""
    print(f"[{label}] FAILED HTTP {resp.status_code}: {error} {description}".rstrip())
    return False


def _list_accessible_customers() -> list[str]:
    from app.services.google_ads import get_google_ads_client

    client = get_google_ads_client()
    service = client.get_service("CustomerService")
    response = service.list_accessible_customers()
    return [name.split("/")[-1] for name in response.resource_names]


async def _check_google_ads_api() -> bool:
    if not settings.google_ads_developer_token:
        print("[GOOGLE_ADS_API] MISSING developer token")
        return False
    try:
        customers = await asyncio.to_thread(_list_accessible_customers)
    except Exception as exc:  # noqa: BLE001 - diagnostic output only
        print(f"[GOOGLE_ADS_API] FAILED: {exc}")
        return False
    print(f"[GOOGLE_ADS_API] OK — {len(customers)} accessible customer(s)")
    return True


async def _first_gsc_token() -> tuple[str, str] | None:
    async with AsyncSessionLocal() as db:
        bu = await db.scalar(
            select(BusinessUnit)
            .where(BusinessUnit.gsc_refresh_token.is_not(None))
            .limit(1)
        )
    if not bu or not bu.gsc_refresh_token:
        return None
    return bu.id, bu.gsc_refresh_token


async def main(strict: bool = False) -> int:
    results: list[bool] = []

    results.append(
        await _check_refresh(
            "GOOGLE_ADS_TOKEN",
            settings.google_ads_client_id,
            settings.google_ads_client_secret,
            settings.google_ads_refresh_token,
        )
    )
    results.append(await _check_google_ads_api())

    gsc = await _first_gsc_token()
    if gsc:
        bu_id, token = gsc
        results.append(
            await _check_refresh(
                f"GSC_TOKEN[{bu_id}]",
                settings.gsc_client_id,
                settings.gsc_client_secret,
                token,
            )
        )
    else:
        print("[GSC_TOKEN] No Business Unit has a stored GSC refresh token")
        results.append(False)

    if strict and not all(results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--strict" in sys.argv)))
