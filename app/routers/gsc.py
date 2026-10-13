"""GSC OAuth handshake & telemetry sync router.

Endpoints:
  GET  /api/v1/business-units/{bu_id}/gsc/auth      → Redirect to Google consent screen
  GET  /api/v1/business-units/{bu_id}/gsc/callback  → Handle OAuth code exchange
  POST /api/v1/business-units/{bu_id}/gsc/sync      → Trigger Search Analytics fetch
  GET  /api/v1/business-units/{bu_id}/gsc/status    → Return current gsc_metrics + connection state
  DELETE /api/v1/business-units/{bu_id}/gsc/disconnect → Revoke & clear tokens
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.business_unit import BusinessUnit
from app.models.user import UserRole
from app.routers.auth import require_role
from app.services.gsc_telemetry import sync_gsc_metrics_for_bu

logger = logging.getLogger("mugogo.gsc_router")

router = APIRouter(
    prefix="/business-units",
    tags=["GSC Telemetry & OAuth"],
)

# ── OAuth constants ────────────────────────────────────────────────────────────
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def _gsc_client_id() -> str:
    return settings.gsc_client_id or settings.google_ads_client_id


def _gsc_client_secret() -> str:
    return settings.gsc_client_secret or settings.google_ads_client_secret


def _callback_uri(bu_id: str) -> str:
    """Build the redirect_uri that must match the Google Cloud Console config."""
    base = settings.app_base_url.rstrip("/")
    return f"{base}/api/v1/business-units/{bu_id}/gsc/callback"


# ── Auth Initiation ────────────────────────────────────────────────────────────

@router.get(
    "/{bu_id}/gsc/auth",
    summary="Initiate Google Search Console OAuth flow",
    response_class=RedirectResponse,
    status_code=302,
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER))],
)
async def gsc_auth_initiate(
    bu_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Redirect the user to Google's OAuth consent screen.

    The `bu_id` is embedded in the OAuth `state` parameter so the callback
    knows which Business Unit to associate the tokens with.
    """
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")

    client_id = _gsc_client_id()
    if not client_id:
        raise HTTPException(
            status_code=500,
            detail=(
                "GSC_CLIENT_ID (or GOOGLE_ADS_CLIENT_ID) is not configured. "
                "Add it to the .env file."
            ),
        )

    params = {
        "client_id": client_id,
        "redirect_uri": _callback_uri(bu_id),
        "response_type": "code",
        "scope": _GSC_SCOPE,
        "access_type": "offline",
        "prompt": "consent",          # Force consent to always receive a refresh_token
        "state": bu_id,               # Carry BU identity through the redirect
    }
    consent_url = f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"
    logger.info("[GSC_OAUTH] Initiating consent flow for BU %s", bu_id)
    return RedirectResponse(url=consent_url, status_code=302)


# ── OAuth Callback ─────────────────────────────────────────────────────────────

@router.get(
    "/{bu_id}/gsc/callback",
    summary="Handle Google OAuth callback and persist tokens",
)
async def gsc_oauth_callback(
    bu_id: str,
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Receive the authorization code from Google, exchange it for tokens,
    and persist them to the BusinessUnit row.

    Note: This endpoint is intentionally unauthenticated (no JWT required) because
    Google redirects here after the user grants consent — the browser won't carry
    our app's Authorization header. Security is maintained by:
      1. Validating `state == bu_id`.
      2. The short-lived authorization code.
      3. Only SUPER_ADMIN/MANAGER can initiate the flow via /gsc/auth.
    """
    if error:
        logger.warning("[GSC_OAUTH] Callback received error for BU %s: %s", bu_id, error)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google OAuth error: {error}",
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No authorization code received from Google.",
        )

    # Validate state matches bu_id
    if state and state != bu_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state mismatch — possible CSRF. Please try again.",
        )

    # Load BU
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")

    # Exchange code → tokens
    token_payload = {
        "code": code,
        "client_id": _gsc_client_id(),
        "client_secret": _gsc_client_secret(),
        "redirect_uri": _callback_uri(bu_id),
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(_TOKEN_URL, data=token_payload)
            resp.raise_for_status()
            token_data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[GSC_OAUTH] Token exchange failed %d: %s",
            exc.response.status_code,
            exc.response.text,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Google token exchange failed: {exc.response.text}",
        )
    except Exception as exc:
        logger.error("[GSC_OAUTH] Token exchange error: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to contact Google token endpoint.")

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "Google did not return a refresh_token. Ensure the OAuth consent "
                "was initiated with access_type=offline&prompt=consent."
            ),
        )

    # Persist tokens
    bu.gsc_access_token = access_token
    bu.gsc_refresh_token = refresh_token

    # If the BU has a GSC site configured, set gsc_site_url too
    if not bu.gsc_site_url and bu.google_search_console_site:
        bu.gsc_site_url = bu.google_search_console_site

    await db.commit()
    await db.refresh(bu)

    logger.info("[GSC_OAUTH] Tokens stored for BU %s. GSC integration active.", bu_id)

    # Automatically kick off an initial sync so the user immediately sees data
    try:
        await sync_gsc_metrics_for_bu(bu_id, db)
    except Exception as exc:
        logger.warning(
            "[GSC_OAUTH] Initial sync after connect failed (non-fatal): %s", exc
        )

    return {
        "status": "success",
        "message": (
            "Google Search Console connected successfully. "
            "Tokens have been stored and an initial data sync has been triggered."
        ),
        "business_unit_id": bu_id,
        "gsc_connected": True,
    }


# ── Sync Trigger ───────────────────────────────────────────────────────────────

@router.post(
    "/{bu_id}/gsc/sync",
    summary="Trigger a fresh GSC Search Analytics sync",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.AGENT))],
)
async def gsc_sync(
    bu_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Fetch the latest 28-day Search Analytics data and update gsc_metrics."""
    try:
        metrics = await sync_gsc_metrics_for_bu(bu_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("[GSC_SYNC] Unexpected error for BU %s", bu_id)
        raise HTTPException(status_code=500, detail=f"GSC sync failed: {exc}")

    return {
        "status": "success",
        "business_unit_id": bu_id,
        "metrics": metrics,
    }


# ── Status / Read ──────────────────────────────────────────────────────────────

@router.get(
    "/{bu_id}/gsc/status",
    summary="Retrieve current GSC connection state and cached metrics",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.AGENT))],
)
async def gsc_status(
    bu_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Return the cached gsc_metrics and connection flags without triggering a new fetch."""
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")

    return {
        "business_unit_id": bu_id,
        "gsc_connected": bool(bu.gsc_refresh_token),
        "gsc_site_url": bu.gsc_site_url or bu.google_search_console_site,
        "metrics": bu.gsc_metrics or {},
    }


# ── Disconnect ─────────────────────────────────────────────────────────────────

@router.delete(
    "/{bu_id}/gsc/disconnect",
    summary="Revoke GSC OAuth tokens and clear integration",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER))],
)
async def gsc_disconnect(
    bu_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Clear stored GSC tokens and wipe the cached metrics for this BU."""
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")

    # Best-effort revoke the access token with Google
    if bu.gsc_access_token:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": bu.gsc_access_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as exc:
            logger.warning("[GSC_DISCONNECT] Token revoke request failed (non-fatal): %s", exc)

    bu.gsc_access_token = None
    bu.gsc_refresh_token = None
    bu.gsc_metrics = {}

    await db.commit()
    logger.info("[GSC_DISCONNECT] GSC integration cleared for BU %s", bu_id)

    return {
        "status": "success",
        "business_unit_id": bu_id,
        "message": "Google Search Console has been disconnected.",
    }
