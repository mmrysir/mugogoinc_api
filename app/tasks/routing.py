from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead, DeliveryStatus, CrmForwardStatus
from app.models.business_unit import BusinessUnit, RouteType
from app.services import whatsapp, webhook, email

logger = logging.getLogger("mugogo.routing")


async def dispatch_lead(
    db: AsyncSession,
    lead: Lead,
    business_unit: BusinessUnit,
) -> None:
    """Async background task — route lead based on BusinessUnit.route_type.

    EXPRESS   → WhatsApp Cloud API alert to owner_whatsapp_number
    ENTERPRISE → HMAC-signed POST to webhook_url (+ email fallback)
    """
    try:
        if business_unit.route_type == RouteType.EXPRESS:
            success = await whatsapp.send_lead_alert(
                owner_whatsapp_number=business_unit.owner_whatsapp_number,
                business_unit_name=business_unit.name,
                full_name=lead.full_name,
                phone_number=lead.phone_number,
                source_channel=lead.source_channel.value,
                extra_meta=lead.payload_meta or None,
            )
            lead.delivery_status = (
                DeliveryStatus.DELIVERED if success else DeliveryStatus.FAILED
            )

        elif business_unit.route_type == RouteType.ENTERPRISE:
            if not business_unit.webhook_url:
                logger.warning(
                    "[ROUTING] ENTERPRISE BU %s has no webhook_url — skipping forward",
                    business_unit.id,
                )
                lead.crm_forward_status = CrmForwardStatus.FAILED
            else:
                lead.crm_forward_status = CrmForwardStatus.QUEUED
                success = await webhook.forward_to_crm(
                    webhook_url=business_unit.webhook_url,
                    lead_id=lead.id,
                    business_unit_id=lead.business_unit_id,
                    full_name=lead.full_name,
                    phone_number=lead.phone_number,
                    email=lead.email,
                    source_channel=lead.source_channel.value,
                    payload_meta=lead.payload_meta,
                )
                lead.crm_forward_status = (
                    CrmForwardStatus.FORWARDED if success else CrmForwardStatus.FAILED
                )

            # Always send backup email for ENTERPRISE route
            await email.send_lead_notification_email(
                to_email=business_unit.owner_email,
                business_unit_name=business_unit.name,
                full_name=lead.full_name,
                phone_number=lead.phone_number,
                source_channel=lead.source_channel.value,
            )
            lead.delivery_status = DeliveryStatus.DELIVERED

        await db.commit()
        logger.info(
            "[ROUTING] Lead %s routed via %s — delivery=%s crm=%s",
            lead.id,
            business_unit.route_type.value,
            lead.delivery_status,
            lead.crm_forward_status,
        )

    except Exception as exc:
        logger.error("[ROUTING] Unhandled error for lead %s: %s", lead.id, exc)
        lead.delivery_status = DeliveryStatus.FAILED
        await db.commit()
