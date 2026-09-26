from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core.config import settings
from app.services.whatsapp import send_whatsapp_message

logger = logging.getLogger("mugogo.campaign_monitor")


async def check_campaign_health(
    campaign_metrics: List[Dict[str, Any]],
    alert_recipient_phone: str,
) -> Dict[str, Any]:
    """
    Campaign Budget Burn & CPL Threshold Monitor.
    Periodically polls ad metrics for client ad spend & leads.
    Triggers automated WhatsApp alerts if:
    1. Daily budget runs out before 2:00 PM.
    2. Cost Per Lead (CPL) exceeds configured threshold.
    """
    now_utc = datetime.now(timezone.utc)
    hour_utc = now_utc.hour

    alerts_triggered = []

    for campaign in campaign_metrics:
        name = campaign.get("campaign_name", "Unknown Campaign")
        spend = float(campaign.get("daily_spend_usd", 0.0))
        budget = float(campaign.get("daily_budget_usd", 0.0))
        leads = int(campaign.get("leads_count", 0))

        cpl = round(spend / leads, 2) if leads > 0 else spend

        # Rule 1: Daily budget burn out before 2:00 PM (14:00)
        if budget > 0 and spend >= (budget * 0.95) and hour_utc < 14:
            alert_text = (
                f"🚨 *Mugogo Engine Alert: Budget Exhausted Early*\n"
                f"Campaign: *{name}*\n"
                f"Spent: ${spend:.2f} / Budget: ${budget:.2f}\n"
                f"Time: {now_utc.strftime('%H:%M UTC')} (Before 2:00 PM)\n"
                f"Action Required: Consider increasing budget or refining targeting."
            )
            alerts_triggered.append(alert_text)
            if alert_recipient_phone:
                await send_whatsapp_message(alert_recipient_phone, alert_text)

        # Rule 2: CPL Exceeds Threshold
        threshold = settings.cpl_threshold_usd
        if leads > 0 and cpl > threshold:
            alert_text = (
                f"⚠️ *Mugogo Engine Alert: High CPL Detected*\n"
                f"Campaign: *{name}*\n"
                f"Current CPL: *${cpl:.2f}* (Threshold: ${threshold:.2f})\n"
                f"Leads: {leads} | Total Spend: ${spend:.2f}\n"
                f"Action Required: Review ad creatives and target keywords."
            )
            alerts_triggered.append(alert_text)
            if alert_recipient_phone:
                await send_whatsapp_message(alert_recipient_phone, alert_text)

    return {
        "checked_at": now_utc.isoformat(),
        "total_campaigns_checked": len(campaign_metrics),
        "alerts_count": len(alerts_triggered),
        "alerts": alerts_triggered,
    }
