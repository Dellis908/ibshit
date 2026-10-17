"""Minimal forward-only schema migrations.

``db.create_all()`` creates missing *tables* but never alters existing ones, so
adding a column to a table that already holds data needs explicit SQL. This
module does exactly that, and nothing more:

* it only ever **adds** columns and indexes — it never drops or rewrites data
* each step is checked against the live schema, so running it repeatedly is safe
* failures are logged and do not stop the app from starting, because a missing
  optional column is better than a service that will not boot

A full migration tool (Alembic) would be the answer for a larger schema. At this
size, an explicit list of additive steps that is easy to read is preferable to a
migration framework.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.extensions import db

log = logging.getLogger(__name__)

#: ``(table, column, DDL type)`` for every column added after the first release.
#: Adding to this list is how a schema change is shipped.
ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Per-user progress arrived after the question bank, so existing attempt rows
    # predate ownership. They are left with a NULL user_id (see _report_orphans)
    # rather than being attributed to an account that did not exist.
    ("attempt", "user_id", "INTEGER"),
    # Subjects arrived with the chemistry bank; existing rows are biology, which
    # is the column default.
    ("topic", "subject", "VARCHAR(20)"),
)

#: Values new columns take on existing rows.
ADDITIVE_DEFAULTS: dict[tuple[str, str], str] = {
    ("topic", "subject"): "biology",
}

#: Indexes created after the fact: ``(index name, table, column)``.
ADDITIVE_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_attempt_user_id", "attempt", "user_id"),
)


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _existing_columns(table: str, engine) -> set[str]:
    if table not in _table_names(engine):
        return set()
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _existing_indexes(table: str, engine) -> set[str]:
    if table not in _table_names(engine):
        return set()
    return {index["name"] for index in inspect(engine).get_indexes(table)}


def run_migrations(engine=None) -> list[str]:
    """Apply every pending additive step. Returns the changes made.

    ``engine`` defaults to the application's, and can be supplied to migrate a
    database that is not the one currently bound — which is what makes this
    testable against a copy of an older schema.
    """
    engine = engine if engine is not None else db.engine
    applied: list[str] = []
    tables = _table_names(engine)

    with engine.begin() as connection:
        for table, column, ddl_type in ADDITIVE_COLUMNS:
            if table not in tables:
                continue  # create_all will build it complete
            if column in _existing_columns(table, engine):
                continue
            try:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
                if (table, column) in ADDITIVE_DEFAULTS:
                    # A new NOT NULL column leaves existing rows NULL; backfill so
                    # queries that filter on it keep finding the old data.
                    connection.execute(
                        text(f"UPDATE {table} SET {column} = :value WHERE {column} IS NULL"),
                        {"value": ADDITIVE_DEFAULTS[(table, column)]},
                    )
                applied.append(f"added {table}.{column}")
                log.info("migration: added %s.%s", table, column)
            except Exception as exc:  # pragma: no cover - depends on the live schema
                log.warning("migration: could not add %s.%s (%s)", table, column, exc)

        for name, table, column in ADDITIVE_INDEXES:
            if table not in tables:
                continue
            if name in _existing_indexes(table, engine):
                continue
            if column not in _existing_columns(table, engine):
                continue
            try:
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
                )
                applied.append(f"created index {name}")
                log.info("migration: created index %s", name)
            except Exception as exc:  # pragma: no cover - depends on the live schema
                log.warning("migration: could not create index %s (%s)", name, exc)

    return applied


def report_orphans() -> int:
    """Count attempts that have no owner, and explain what that means.

    Rows written before accounts existed cannot be attributed to anyone. They are
    reported once so the operator knows the history is still there, rather than
    silently ignored or wrongly assigned.
    """
    from app.models import Attempt

    if "attempt" not in _table_names(db.engine):
        return 0
    if "user_id" not in _existing_columns("attempt", db.engine):
        return 0

    try:
        orphans = db.session.query(Attempt).filter(Attempt.user_id.is_(None)).count()
    except Exception:  # pragma: no cover - defensive
        return 0

    if orphans:
        log.warning(
            "migration: %s answer(s) were recorded before accounts existed and have "
            "no owner. They are kept but not shown to any user. To hand them to an "
            "account, run: flask auth adopt-attempts <email>",
            orphans,
        )
    return orphans
