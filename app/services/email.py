from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("mugogo.email")


async def send_lead_notification_email(
    to_email: str,
    business_unit_name: str,
    full_name: str,
    phone_number: str,
    source_channel: str,
) -> bool:
    """Send transactional lead notification via Resend API.

    Stub mode when RESEND_API_KEY is not configured.
    """
    if not settings.resend_api_key:
        logger.info(
            "[EMAIL STUB] Would email %s | Lead: %s (%s) via %s",
            to_email,
            full_name,
            phone_number,
            source_channel,
        )
        return True

    html_body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto">
      <h2 style="color:#0f172a">🔔 New Lead — {business_unit_name}</h2>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:8px;font-weight:bold">Name</td><td>{full_name}</td></tr>
        <tr style="background:#f8fafc">
          <td style="padding:8px;font-weight:bold">Phone</td><td>{phone_number}</td>
        </tr>
        <tr><td style="padding:8px;font-weight:bold">Source</td>
          <td>{source_channel.replace("_", " ").title()}</td>
        </tr>
      </table>
      <p style="color:#64748b;font-size:12px;margin-top:24px">
        Powered by Mugogo Inc. Engine
      </p>
    </div>
    """

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                json={
                    "from": settings.email_from_address,
                    "to": [to_email],
                    "subject": f"New Lead: {full_name} — {business_unit_name}",
                    "html": html_body,
                },
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            )
            resp.raise_for_status()
            logger.info("[EMAIL] Notification sent to %s", to_email)
            return True
    except httpx.HTTPError as exc:
        logger.error("[EMAIL] Failed to send to %s: %s", to_email, exc)
        return False
