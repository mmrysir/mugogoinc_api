from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


is_sqlite = "sqlite" in settings.database_url

engine_kwargs = {
    "echo": settings.app_env == "development",
    "future": True,
}

if is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL Connection Pooling for high concurrency
    engine_kwargs["pool_size"] = 20
    engine_kwargs["max_overflow"] = 10
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 1800

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
    """Create all tables on first boot and repair known column drift.

    ``Base.metadata.create_all`` only creates missing tables; it never adds
    columns to tables that already exist. Since this project predates Alembic,
    we perform a lightweight, idempotent column-drift repair for existing
    databases so that ORM queries and the physical schema stay in sync.
    """
    from app.models import organization, business_unit, lead, user, post  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_repair_schema_drift)


def _repair_schema_drift(sync_conn) -> None:
    """Add columns that the ORM expects but that are missing in older DB files."""
    inspector = inspect(sync_conn)

    if "organizations" in inspector.get_table_names():
        existing_columns = {c["name"] for c in inspector.get_columns("organizations")}
        if "created_by_id" not in existing_columns:
            sync_conn.execute(
                text(
                    "ALTER TABLE organizations "
                    "ADD COLUMN created_by_id VARCHAR(36) REFERENCES users(id)"
                )
            )
        if "api_key" not in existing_columns:
            sync_conn.execute(
                text(
                    "ALTER TABLE organizations "
                    "ADD COLUMN api_key VARCHAR(255)"
                )
            )
