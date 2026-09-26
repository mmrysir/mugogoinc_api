import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal, engine, Base
from app.models.organization import Organization
from app.models.business_unit import BusinessUnit
import uuid
from datetime import datetime, timezone

async def main():
    async with AsyncSessionLocal() as db:
        # Check for BU
        bu_result = await db.execute(select(BusinessUnit).limit(1))
        bu = bu_result.scalar_one_or_none()
        if bu:
            print(f"BU_ID={bu.id}")
            return
            
        # Create Org and BU if none
        org = Organization(
            id=str(uuid.uuid4()),
            name="Test Org",
            status="ACTIVE",
            created_at=datetime.now(timezone.utc)
        )
        db.add(org)
        await db.flush()
        
        bu = BusinessUnit(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name="Test BU",
            route_type="EXPRESS",
            owner_whatsapp_number="+254700000000",
            owner_email="test@test.com",
            created_at=datetime.now(timezone.utc)
        )
        db.add(bu)
        await db.commit()
        print(f"BU_ID={bu.id}")

if __name__ == "__main__":
    asyncio.run(main())

