"""GSC OAuth handshake & telemetry sync router.

Endpoints:
  GET  /api/v1/business-units/{bu_id}/gsc/auth-url      → Return Google consent URL
  GET  /api/v1/gsc/callback                             → Google OAuth callback (public)
  GET  /api/v1/business-units/{bu_id}/gsc/callback     → Legacy per-BU callback (delegates)
  POST /api/v1/business-units/{bu_id}/gsc/sync         → Trigger Search Analytics fetch
  GET  /api/v1/business-units/{bu_id}/gsc/status       → Return current gsc_metrics + connection state
  DELETE /api/v1/business-units/{bu_id}/gsc/disconnect → Revoke & clear tokens
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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

# Public (unauthenticated) OAuth callback. Google redirects the browser here, so
# it must not sit behind the JWT dependency. A single fixed callback path is used
# for both the consent initiation and the token exchange: Google only accepts
# exact, pre-registered redirect URIs and does not support wildcards, so a
# per-Business-Unit path could never be registered.
public_router = APIRouter(
    prefix="/gsc",
    tags=["GSC OAuth"],
)

# ── OAuth constants ────────────────────────────────────────────────────────────
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

# Single redirect URI registered once in Google Cloud Console.
_FIXED_CALLBACK_PATH = "/api/v1/gsc/callback"


def _gsc_client_id() -> str:
    """GSC web client ID — never falls back to the Desktop Google Ads client."""
    client_id = settings.gsc_client_id
    if not client_id:
        raise HTTPException(
            status_code=500,
            detail="GSC_CLIENT_ID is not configured on the server. Add it to .env and redeploy.",
        )
    return client_id


def _gsc_client_secret() -> str:
    """GSC web client secret — never falls back to the Desktop Google Ads client."""
    secret = settings.gsc_client_secret
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="GSC_CLIENT_SECRET is not configured on the server. Add it to .env and redeploy.",
        )
    return secret


def _base_url(request: Request) -> str:
    """Base URL of the API as seen by Google during the OAuth handshake."""
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and not any(
        host in base for host in ("localhost", "127.0.0.1", "0.0.0.0")
    ):
        base = "https://" + base[len("http://") :]
    return base


def _callback_uri(request: Request) -> str:
    """Fixed OAuth redirect URI used for both auth-url and callback."""
    return f"{_base_url(request)}{_FIXED_CALLBACK_PATH}"


def _sign_state(bu_id: str) -> str:
    """Signed, stateless OAuth state: ``<bu_id>.<hmac-sha256-token>``."""
    digest = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        bu_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    token = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return f"{bu_id}.{token}"


def _verify_state(state: Optional[str]) -> Optional[str]:
    """Return the Business Unit id when the state signature is valid."""
    if not state or "." not in state:
        return None
    bu_id, _, token = state.rpartition(".")
    expected = base64.urlsafe_b64encode(
        hmac.new(
            settings.app_secret_key.encode("utf-8"),
            bu_id.encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    ).decode("utf-8").rstrip("=")
    if not token or not hmac.compare_digest(token, expected):
        return None
    return bu_id


# ── Auth URL (authenticated JSON endpoint) ───────────────────────────────────

@router.get(
    "/{bu_id}/gsc/auth-url",
    summary="Get the Google OAuth consent URL for GSC connection",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER))],
)
async def gsc_auth_url(
    bu_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Return the Google OAuth consent URL as JSON.

    The frontend fetches this with its JWT (Authorization header), then
    navigates to the returned URL via window.location.href — solving the
    problem of browser navigations not being able to carry Bearer tokens.
    """
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")

    client_id = _gsc_client_id()  # raises HTTP 500 if GSC_CLIENT_ID not set

    params = {
        "client_id": client_id,
        "redirect_uri": _callback_uri(request),
        "response_type": "code",
        "scope": _GSC_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": _sign_state(bu_id),
    }
    consent_url = f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"
    logger.info("[GSC_OAUTH] Auth URL generated for BU %s", bu_id)
    return {"auth_url": consent_url}


# ── OAuth Callback (shared exchange logic) ─────────────────────────────────────

async def _exchange_gsc_oauth(
    bu_id: str,
    code: Optional[str],
    request: Request,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Exchange the Google authorization code and persist tokens for a BU.

    Raises HTTPException on failure; returns the success payload on success.
    Shared by the public fixed callback and the legacy per-BU callback routes.
    """
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
        "redirect_uri": _callback_uri(request),
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
        "organization_id": bu.organization_id,
        "gsc_connected": True,
    }


# ── OAuth Callback (legacy per-BU path — kept for backward compatibility) ─────

@router.get(
    "/{bu_id}/gsc/callback",
    summary="Handle Google OAuth callback and persist tokens",
)
async def gsc_oauth_callback(
    bu_id: str,
    request: Request,
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
      1. Validating a signed `state` that encodes the BU id.
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

    resolved_bu_id = _verify_state(state) if state else None
    if not resolved_bu_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state mismatch — possible CSRF. Please try again.",
        )
    bu_id = resolved_bu_id

    return await _exchange_gsc_oauth(bu_id, code, request, db)


# ── OAuth Callback (public, fixed redirect URI) ─────────────────────────────────

@public_router.get(
    "/callback",
    summary="Handle Google OAuth callback after the browser is redirected back from Google",
)
async def gsc_oauth_callback_public(
    request: Request,
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Google redirects the browser here after consent. Exchanges the code and
    redirects the user back to their Business Unit page, carrying a status flag.
    """
    resolved_bu_id = _verify_state(state) if state else None

    if error:
        logger.warning("[GSC_OAUTH] Callback received error for BU %s: %s", resolved_bu_id, error)
        return RedirectResponse(
            f"{settings.frontend_url.rstrip('/')}"
            f"/organizations?gsc_error={urlencode({'bu_id': resolved_bu_id or '', 'error': error})}"
        )

    if not resolved_bu_id:
        logger.warning("[GSC_OAUTH] Callback without valid state: %s", error or "no state")
        return RedirectResponse(
            f"{settings.frontend_url.rstrip('/')}"
            f"/organizations?gsc_error={urlencode({'bu_id': '', 'error': error or 'invalid_state'})}"
        )

    try:
        result = await _exchange_gsc_oauth(resolved_bu_id, code, request, db)
    except HTTPException as exc:
        logger.warning("[GSC_OAUTH] Callback failed for BU %s: %s", resolved_bu_id, exc.detail)
        return RedirectResponse(
            f"{settings.frontend_url.rstrip('/')}"
            f"/organizations?gsc_error={urlencode({'bu_id': resolved_bu_id, 'error': str(exc.detail)})}"
        )

    logger.info("[GSC_OAUTH] Callback succeeded for BU %s", resolved_bu_id)
    return RedirectResponse(
        f"{settings.frontend_url.rstrip('/')}"
        f"/organizations/{result.get('organization_id', '')}"
        f"/business-units/{resolved_bu_id}?gsc_connected=1"
    )


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
