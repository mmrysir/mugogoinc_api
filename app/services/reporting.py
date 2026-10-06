"""
Automated WhatsApp Reporting Service — Mugogo Engine.

Generates and dispatches weekly performance summary reports and real-time
lead alert digests via the existing WhatsApp Cloud API integration.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.models.organization import Organization
from app.models.business_unit import BusinessUnit
from app.services.whatsapp import send_whatsapp_message

logger = logging.getLogger("mugogo.reporting")


async def build_weekly_summary(db: AsyncSession, org_id: Optional[str] = None) -> dict:
    """
    Aggregates lead metrics for the past 7 days.
    If org_id is provided, scopes to that organization only.
    """
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    # Base query: leads from the past 7 days
    base = select(Lead).where(Lead.created_at >= week_ago)
    if org_id:
        # Filter via the BusinessUnit → Organization join
        base = (
            select(Lead)
            .join(BusinessUnit, Lead.business_unit_id == BusinessUnit.id)
            .where(
                Lead.created_at >= week_ago,
                BusinessUnit.organization_id == org_id,
            )
        )

    result = await db.execute(base)
    leads = result.scalars().all()

    total         = len(leads)
    delivered     = sum(1 for l in leads if l.delivery_status == "DELIVERED")
    failed        = sum(1 for l in leads if l.delivery_status == "FAILED")
    returning     = sum(1 for l in leads if l.is_returning)
    channels: dict[str, int] = {}
    for l in leads:
        channels[l.source_channel] = channels.get(l.source_channel, 0) + 1

    delivery_rate = round((delivered / total * 100), 1) if total else 0.0

    return {
        "period_start": week_ago.strftime("%d %b %Y"),
        "period_end":   now.strftime("%d %b %Y"),
        "total_leads":  total,
        "delivered":    delivered,
        "failed":       failed,
        "returning":    returning,
        "delivery_rate_pct": delivery_rate,
        "channels": channels,
    }


def format_weekly_report(summary: dict, org_name: str = "All Organizations") -> str:
    """Render a clean, emoji-rich WhatsApp message from the summary dict."""
    channel_lines = "\n".join(
        f"  • {ch.replace('_', ' ').title()}: {count}"
        for ch, count in summary["channels"].items()
    ) or "  • No channel data"

    return (
        f"📊 *Mugogo Engine — Weekly Report*\n"
        f"*{org_name}*\n"
        f"Period: {summary['period_start']} → {summary['period_end']}\n\n"
        f"*Lead Performance*\n"
        f"  🎯 Total Leads: *{summary['total_leads']}*\n"
        f"  ✅ Delivered:   *{summary['delivered']}*\n"
        f"  ❌ Failed:      *{summary['failed']}*\n"
        f"  🔄 Returning:   *{summary['returning']}*\n"
        f"  📈 Delivery Rate: *{summary['delivery_rate_pct']}%*\n\n"
        f"*Source Breakdown*\n"
        f"{channel_lines}\n\n"
        f"_Powered by Mugogo Inc. Engine — {datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')}_"
    )


async def dispatch_weekly_report(
    db: AsyncSession,
    recipient_phone: str,
    org_id: Optional[str] = None,
    org_name: str = "All Organizations",
) -> dict:
    """
    Builds the weekly summary and dispatches it to the given WhatsApp number.
    Returns a status dict with the summary and dispatch result.
    """
    summary = await build_weekly_summary(db, org_id=org_id)
    message = format_weekly_report(summary, org_name=org_name)

    sent = await send_whatsapp_message(recipient_phone, message)

    logger.info(
        "[Reporting] Weekly report dispatched to %s | org=%s | sent=%s",
        recipient_phone, org_id, sent
    )

    return {
        "dispatched": sent,
        "recipient": recipient_phone,
        "org_id": org_id,
        "summary": summary,
        "message_preview": message[:200] + "…" if len(message) > 200 else message,
    }
