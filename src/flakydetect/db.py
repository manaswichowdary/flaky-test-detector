"""Database schema (SQLAlchemy Core). Works on SQLite (local/tests) and Postgres (CI/prod).

All timestamps are stored as naive UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
    UniqueConstraint, create_engine, event,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True),
    Column("repo", String(200), nullable=False),
    Column("run_id", String(100), nullable=False),
    Column("run_attempt", Integer, nullable=False, default=1),
    Column("sha", String(64), nullable=False),
    Column("branch", String(255)),
    Column("pr_number", Integer),
    Column("workflow", String(255)),
    Column("source", String(20), nullable=False, default="ci"),
    Column("started_at", DateTime),
    Column("completed_at", DateTime),
    Column("ingested_at", DateTime, nullable=False),
    UniqueConstraint("repo", "run_id", "run_attempt", name="uq_run"),
)

tests = Table(
    "tests", metadata,
    Column("id", Integer, primary_key=True),
    Column("repo", String(200), nullable=False),
    Column("test_id", String(1000), nullable=False),
    Column("fingerprint", String(64)),
    Column("first_seen", DateTime, nullable=False),
    Column("last_seen", DateTime, nullable=False),
    UniqueConstraint("repo", "test_id", name="uq_test"),
)

test_aliases = Table(
    "test_aliases", metadata,
    Column("id", Integer, primary_key=True),
    Column("repo", String(200), nullable=False),
    Column("alias", String(1000), nullable=False),
    Column("test_pk", Integer, ForeignKey("tests.id", ondelete="CASCADE"), nullable=False),
    Column("reason", String(50), nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("repo", "alias", name="uq_alias"),
)

test_results = Table(
    "test_results", metadata,
    Column("id", Integer, primary_key=True),
    Column("run_pk", Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("test_pk", Integer, ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("raw_test_id", String(1000), nullable=False),
    Column("status", String(10), nullable=False),
    Column("duration", Float, nullable=False, default=0.0),
    Column("rerun_failures", Integer, nullable=False, default=0),
    Column("message", Text),
    UniqueConstraint("run_pk", "raw_test_id", name="uq_result"),
)

test_scores = Table(
    "test_scores", metadata,
    Column("test_pk", Integer, ForeignKey("tests.id", ondelete="CASCADE"), primary_key=True),
    Column("score", Float, nullable=False),
    Column("mixed_commits", Integer, nullable=False),
    Column("rerun_passes", Integer, nullable=False),
    Column("commits_seen", Integer, nullable=False),
    Column("runs_seen", Integer, nullable=False),
    Column("failures", Integer, nullable=False),
    Column("is_flaky", Boolean, nullable=False),
    Column("last_failure_at", DateTime),
    Column("updated_at", DateTime, nullable=False),
)

quarantine_issues = Table(
    "quarantine_issues", metadata,
    Column("test_pk", Integer, ForeignKey("tests.id", ondelete="CASCADE"), primary_key=True),
    Column("issue_number", Integer, nullable=False),
    Column("issue_url", String(500), nullable=False),
    Column("state", String(10), nullable=False, default="open"),
    Column("failure_at", DateTime),
    Column("created_at", DateTime, nullable=False),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def get_engine(url: str) -> Engine:
    engine = create_engine(url, future=True, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
