from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.business_unit import BusinessUnit
from app.models.lead import DeliveryStatus, Lead, SourceChannel
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.routers.auth import get_current_user
from app.schemas.lead import LeadListResponse, LeadResponse
from app.utils.privacy import mask_email, mask_name, mask_phone

router = APIRouter(prefix="/leads", tags=["Leads"], dependencies=[Depends(get_current_user)])


# ── PII Serialization ─────────────────────────────────────────────────────────

def _serialize_lead(lead: Lead, can_view_pii: bool) -> LeadResponse:
    resp = LeadResponse.model_validate(lead)
    if not can_view_pii:
        resp.is_blurred = True
        resp.full_name = mask_name(lead.full_name)
        resp.phone_number = mask_phone(lead.phone_number)
        resp.email = mask_email(lead.email)
        # Mask IP address — personal data under GDPR / data minimisation
        if resp.ip_address:
            resp.ip_address = "●●●.●●●.●●●.●●●"
    return resp


# ── Org-Scope Subquery Builder ────────────────────────────────────────────────

def _agent_permitted_bu_ids(user_id: str):
    """
    Return a subquery of BusinessUnit.id values the given agent is permitted
    to access, by unioning:
      1. Organizations via the new agent_org_assignments junction table.
      2. Organizations via the legacy assigned_agent_id FK (backward compat).
    """
    # Import here to avoid circular-import risk during Sprint 2 model addition.
    # Once agent_org_assignments table exists, this will resolve correctly.
    try:
        from app.models.agent_assignment import AgentOrgAssignment
        junction_org_ids = select(AgentOrgAssignment.org_id).where(
            AgentOrgAssignment.agent_id == user_id
        )
    except ImportError:
        # Sprint 2 model not yet created — fall back to legacy-only
        junction_org_ids = select(Organization.id).where(False)  # empty set

    legacy_org_ids = select(Organization.id).where(
        Organization.assigned_agent_id == user_id
    )
    all_org_ids = union(junction_org_ids, legacy_org_ids).subquery()

    return select(BusinessUnit.id).where(
        BusinessUnit.organization_id.in_(select(all_org_ids.c[0]))
    ).subquery()


def _manager_permitted_bu_ids(user_id: str):
    """Return a subquery of BusinessUnit.id values a Manager may access."""
    manager_org_ids = select(Organization.id).where(
        (Organization.created_by_id == user_id) |
        (Organization.assigned_agent_id == user_id)
    ).subquery()

    return select(BusinessUnit.id).where(
        BusinessUnit.organization_id.in_(select(manager_org_ids.c[0]))
    ).subquery()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=LeadListResponse)
async def list_leads(
    business_unit_id: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    delivery_status: Optional[DeliveryStatus] = Query(None),
    source_channel: Optional[SourceChannel] = Query(None),
    date_from: Optional[datetime] = Query(
        None, description="ISO 8601 datetime – filter leads created at or after this time"
    ),
    date_to: Optional[datetime] = Query(
        None, description="ISO 8601 datetime – filter leads created before or at this time"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LeadListResponse:
    from sqlalchemy import func

    q = select(Lead)

    # ── Mandatory role-based org scope (applied before any caller filters) ─────
    if current_user.role == UserRole.AGENT:
        if current_user.is_approved and not current_user.is_frozen:
            # Approved agents: strictly scoped to their assigned organizations only.
            # This is the primary security boundary — prevents cross-tenant data access.
            permitted_bu_ids = _agent_permitted_bu_ids(current_user.id)
            q = q.where(Lead.business_unit_id.in_(select(permitted_bu_ids.c[0])))
            can_view_pii = True
        else:
            # Unapproved / frozen agents: can see all leads but PII is always blurred.
            # No org-scope restriction needed — blurring is the safety layer here.
            can_view_pii = False
    elif current_user.role == UserRole.MANAGER:
        permitted_bu_ids = _manager_permitted_bu_ids(current_user.id)
        q = q.where(Lead.business_unit_id.in_(select(permitted_bu_ids.c[0])))
        can_view_pii = True
    else:
        # SUPER_ADMIN — unrestricted
        can_view_pii = True

    # ── Optional caller-supplied filters (applied on top of role scope) ────────
    if business_unit_id:
        q = q.where(Lead.business_unit_id == business_unit_id)

    if organization_id:
        q = q.join(BusinessUnit, Lead.business_unit_id == BusinessUnit.id).where(
            BusinessUnit.organization_id == organization_id
        )

    if delivery_status:
        q = q.where(Lead.delivery_status == delivery_status)

    if source_channel:
        q = q.where(Lead.source_channel == source_channel)

    if date_from:
        q = q.where(Lead.created_at >= date_from)

    if date_to:
        q = q.where(Lead.created_at <= date_to)

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        q.order_by(Lead.last_activity_at.desc()).offset(skip).limit(limit)
    )
    items = result.scalars().all()

    lead_responses = [_serialize_lead(lead, can_view_pii) for lead in items]
    return LeadListResponse(total=total, items=lead_responses)


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LeadResponse:
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # ── Ownership check — validate lead is within the caller's permitted scope ──
    if current_user.role == UserRole.AGENT:
        if current_user.is_approved and not current_user.is_frozen:
            # Resolve which org this lead's BU belongs to
            bu_result = await db.execute(
                select(BusinessUnit).where(BusinessUnit.id == lead.business_unit_id)
            )
            bu = bu_result.scalar_one_or_none()
            if not bu:
                raise HTTPException(status_code=404, detail="Lead not found")

            # Check junction table assignment (Sprint 2+)
            is_assigned = False
            try:
                from app.models.agent_assignment import AgentOrgAssignment
                junction_check = await db.scalar(
                    select(AgentOrgAssignment).where(
                        AgentOrgAssignment.agent_id == current_user.id,
                        AgentOrgAssignment.org_id == bu.organization_id,
                    )
                )
                is_assigned = junction_check is not None
            except ImportError:
                pass

            # Fallback: check legacy assigned_agent_id FK
            if not is_assigned:
                legacy_check = await db.scalar(
                    select(Organization).where(
                        Organization.id == bu.organization_id,
                        Organization.assigned_agent_id == current_user.id,
                    )
                )
                is_assigned = legacy_check is not None

            if not is_assigned:
                # Return 404 (not 403) to avoid leaking existence of the lead
                raise HTTPException(status_code=404, detail="Lead not found")

            can_view_pii = True
        else:
            # Unapproved / frozen agents: can fetch any lead, always blurred
            can_view_pii = False

    elif current_user.role == UserRole.MANAGER:
        # Managers can access leads in their created/assigned orgs
        bu_result = await db.execute(
            select(BusinessUnit).where(BusinessUnit.id == lead.business_unit_id)
        )
        bu = bu_result.scalar_one_or_none()
        if not bu:
            raise HTTPException(status_code=404, detail="Lead not found")
        org = await db.scalar(
            select(Organization).where(
                Organization.id == bu.organization_id,
                (Organization.created_by_id == current_user.id) |
                (Organization.assigned_agent_id == current_user.id),
            )
        )
        if not org:
            raise HTTPException(status_code=404, detail="Lead not found")
        can_view_pii = True

    else:
        # SUPER_ADMIN — unrestricted
        can_view_pii = True

    return _serialize_lead(lead, can_view_pii)
