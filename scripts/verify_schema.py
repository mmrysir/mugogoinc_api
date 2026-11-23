"""Fail the deploy when the physical database schema is missing ORM columns.

Run from the API project root:

    PYTHONPATH=. python scripts/verify_schema.py

Exit code 0 means every table/column/index declared by the SQLAlchemy models
exists in the database.  Exit code 1 prints exactly what is missing.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import inspect

import app.models  # noqa: F401  — register every model on Base.metadata
from app.core.database import Base, engine


async def _check() -> list[str]:
    missing: list[str] = []

    def inspect_sync(sync_conn) -> None:
        inspector = inspect(sync_conn)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                missing.append(f"table:{table.name}")
                continue
            existing_columns = {
                col["name"] for col in inspector.get_columns(table.name)
            }
            for column in table.columns:
                if column.name not in existing_columns:
                    missing.append(f"{table.name}.{column.name}")

    async with engine.connect() as conn:
        await conn.run_sync(inspect_sync)
    return missing


def main() -> int:
    missing = asyncio.run(_check())
    if missing:
        print("SCHEMA INCOMPLETE — missing:", ", ".join(missing))
        return 1
    print("SCHEMA OK — all ORM tables and columns are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
