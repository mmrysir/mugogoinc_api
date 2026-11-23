from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.schema_sync import repair_schema_drift

logger = logging.getLogger("mugogo.database")

# Advisory lock key used to serialise schema bootstrap across the multiple
# uvicorn workers (and across concurrent deploys).
_SCHEMA_LOCK_KEY = 0x4D55474F  # "MUGO"


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


engine_kwargs = {
    "echo": settings.app_env == "development",
    "future": True,
    "pool_size": 20,
    "max_overflow": 10,
    "pool_pre_ping": True,
    "pool_recycle": 1800,
}

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    **engine_kwargs
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_all_tables() -> None:
    """Create missing tables and repair column drift on every boot.

    ``Base.metadata.create_all`` only creates missing tables; it never adds
    columns to tables that already exist.  ``repair_schema_drift`` closes that
    gap so the physical schema always matches the ORM.

    A PostgreSQL transaction-scoped advisory lock serialises this work across
    uvicorn workers, preventing concurrent ``ALTER TABLE`` races.
    """
    # Import the package (not individual modules) so *every* model is
    # registered on ``Base.metadata`` before we diff it against the database.
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _SCHEMA_LOCK_KEY},
            )
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(repair_schema_drift, Base.metadata)
