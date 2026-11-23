"""Schema-drift detection and repair utilities.

The production database predates Alembic: it was originally created with
``Base.metadata.create_all``.  ``create_all`` creates missing *tables* but
never adds columns to tables that already exist, so live databases can drift
away from the ORM definition whenever a model gains a column.

This module provides:

* ``repair_schema_drift`` — add any ORM column that is missing from an
  existing table.  Repairs are nullable and default-free so they can never
  fail because of pre-existing rows.
* Idempotent Alembic helpers (``add_column_if_missing`` etc.) so the
  migration chain can be replayed against a drifted database without
  aborting on "column already exists" errors.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy import Column, ForeignKey, inspect, text
from sqlalchemy.schema import CreateColumn

logger = logging.getLogger("mugogo.schema")


# ── ORM → DDL helpers ────────────────────────────────────────────────────────

def _scalar_default(column: Column) -> str | None:
    """Return a SQL literal for a simple Python-side default, if safe.

    Only booleans/numbers/strings are emitted.  Callables (e.g.
    ``default=lambda: datetime.now(...)``) and enum members are skipped
    because they cannot be safely inlined into DDL.
    """
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    arg = default.arg
    if isinstance(arg, bool):
        return "true" if arg else "false"
    if isinstance(arg, (int, float)):
        return str(arg)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    return None


def _add_column_sql(column: Column, dialect: Any) -> str:
    """Render ``<name> <type> [DEFAULT ...] [REFERENCES ...]``.

    The generated column is always nullable so the repair is safe on tables
    that already contain rows.  A constant default is included when the ORM
    declares one (e.g. ``is_approved=False``) so existing rows get a sane
    value instead of NULL.
    """
    default_sql = _scalar_default(column)
    kwargs: dict[str, Any] = {"nullable": True}
    if default_sql is not None:
        kwargs["server_default"] = text(default_sql)
    new_column = Column(column.name, column.type, **kwargs)
    for fk in column.foreign_keys:
        new_column.append_foreign_key(
            ForeignKey(fk.target_fullname, ondelete=fk.ondelete)
        )
    return str(CreateColumn(new_column).compile(dialect=dialect))


def repair_schema_drift(sync_conn, metadata) -> None:
    """Add missing columns and indexes to tables that already exist.

    Runs inside a transaction.  Each individual repair uses a SAVEPOINT so a
    single failure cannot abort the whole transaction (and therefore cannot
    prevent the application from starting).
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    repaired: list[str] = []
    failed: list[str] = []

    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        existing_columns = {
            col["name"] for col in inspector.get_columns(table.name)
        }

        for column in table.columns:
            if column.name in existing_columns:
                continue
            ddl = _add_column_sql(column, sync_conn.dialect)
            try:
                with sync_conn.begin_nested():
                    sync_conn.execute(
                        text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')
                    )
                repaired.append(f"{table.name}.{column.name}")
            except Exception as exc:  # pragma: no cover - defensive
                failed.append(f"{table.name}.{column.name} ({exc})")

        # Indexes are also part of the ORM definition.  Missing indexes do not
        # break queries but they do cause severe performance regressions.
        existing_indexes = {
            idx["name"] for idx in inspector.get_indexes(table.name)
        }
        for index in table.indexes:
            if not index.name or index.name in existing_indexes:
                continue
            try:
                with sync_conn.begin_nested():
                    index.create(sync_conn)
                repaired.append(f"index {index.name}")
            except Exception as exc:  # pragma: no cover - defensive
                failed.append(f"index {index.name} ({exc})")

    if repaired:
        logger.warning(
            "🔧 Repaired %d schema drift item(s): %s",
            len(repaired),
            ", ".join(repaired),
        )
    if failed:
        logger.error(
            "❌ Could not repair %d schema drift item(s): %s",
            len(failed),
            "; ".join(failed),
        )


# ── Idempotent Alembic helpers ───────────────────────────────────────────────

def _inspector():
    # Imported lazily so this module can also be imported outside Alembic.
    from alembic import op

    return inspect(op.get_bind())


def _column_names(inspector, table_name: str) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def create_table_if_missing(table_name: str, *columns, **kwargs) -> None:
    """``op.create_table`` that silently no-ops when the table exists."""
    from alembic import op

    if not _inspector().has_table(table_name):
        op.create_table(table_name, *columns, **kwargs)


def add_column_if_missing(table_name: str, column: Column) -> None:
    """``op.add_column`` that silently no-ops when the column exists."""
    from alembic import op

    if column.name not in _column_names(_inspector(), table_name):
        op.add_column(table_name, column)


def create_index_if_missing(
    index_name: str,
    table_name: str,
    columns: Iterable[str],
    **kwargs,
) -> None:
    """``op.create_index`` that silently no-ops when the index exists."""
    from alembic import op

    inspector = _inspector()
    if not inspector.has_table(table_name):
        return
    existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
    if str(index_name) in existing:
        return
    op.create_index(index_name, table_name, columns, **kwargs)


def alter_column_if_exists(table_name: str, column_name: str, **kwargs) -> None:
    """``op.alter_column`` that silently no-ops when the column is absent."""
    from alembic import op

    if column_name not in _column_names(_inspector(), table_name):
        return
    op.alter_column(table_name, column_name, **kwargs)
