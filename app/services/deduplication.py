from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import sha256_hex
from app.models.lead import Lead


async def compute_dedupe_hash(phone_number: str, business_unit_id: str) -> str:
    """Compute SHA256(phone_number + business_unit_id)."""
    return await sha256_hex(phone_number + business_unit_id)


async def find_duplicate(
    db: AsyncSession,
    dedupe_hash: str,
    window_hours: int = 24,
) -> Optional[Lead]:
    """Return the most recent Lead matching *dedupe_hash* within the time window, or None."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    stmt = (
        select(Lead)
        .where(
            and_(
                Lead.dedupe_hash == dedupe_hash,
                Lead.created_at >= cutoff,
            )
        )
        .order_by(Lead.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
