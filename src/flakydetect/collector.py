"""Store one row per test per CI run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine

from . import db
from .junit import TestResult, merge_attempts

# (conn, repo, result, run_completed_at, raw ids in this run) -> canonical tests.id
Resolver = Callable[[Connection, str, TestResult, datetime, frozenset[str]], int]


@dataclass
class RunMeta:
    run_id: str
    sha: str
    run_attempt: int = 1
    branch: str | None = None
    pr_number: int | None = None
    workflow: str | None = None
    source: str = "ci"
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class IngestResult:
    run_pk: int
    inserted: int
    duplicate: bool = False


def get_or_create_test(conn: Connection, repo: str, test_id: str, seen_at: datetime,
                       fingerprint: str | None = None) -> int:
    row = conn.execute(
        select(db.tests.c.id).where(db.tests.c.repo == repo, db.tests.c.test_id == test_id)
    ).first()
    if row:
        conn.execute(update(db.tests).where(db.tests.c.id == row.id).values(last_seen=seen_at))
        return row.id
    return conn.execute(
        insert(db.tests).values(
            repo=repo, test_id=test_id, fingerprint=fingerprint,
            first_seen=seen_at, last_seen=seen_at,
        )
    ).inserted_primary_key[0]


def exact_resolver(conn: Connection, repo: str, result: TestResult, seen_at: datetime,
                   current_ids: frozenset[str] = frozenset()) -> int:
    return get_or_create_test(conn, repo, result.test_id, seen_at, result.properties.get("fingerprint"))


def ingest(engine: Engine, repo: str, meta: RunMeta, results: list[TestResult],
           resolver: Resolver | None = None) -> IngestResult:
    resolver = resolver or exact_resolver
    results = merge_attempts(list(results))
    now = db.utcnow()
    completed = db.to_naive_utc(meta.completed_at) or now
    with engine.begin() as conn:
        existing = conn.execute(
            select(db.runs.c.id).where(
                db.runs.c.repo == repo, db.runs.c.run_id == str(meta.run_id),
                db.runs.c.run_attempt == meta.run_attempt,
            )
        ).first()
        if existing:
            return IngestResult(run_pk=existing.id, inserted=0, duplicate=True)
        run_pk = conn.execute(
            insert(db.runs).values(
                repo=repo, run_id=str(meta.run_id), run_attempt=meta.run_attempt, sha=meta.sha,
                branch=meta.branch, pr_number=meta.pr_number, workflow=meta.workflow,
                source=meta.source, started_at=db.to_naive_utc(meta.started_at),
                completed_at=completed, ingested_at=now,
            )
        ).inserted_primary_key[0]
        current_ids = frozenset(r.test_id for r in results)
        rows = [
            {
                "run_pk": run_pk,
                "test_pk": resolver(conn, repo, r, completed, current_ids),
                "raw_test_id": r.test_id,
                "status": r.status,
                "duration": r.duration,
                "rerun_failures": r.rerun_failures,
                "message": r.message,
            }
            for r in results
        ]
        if rows:
            conn.execute(insert(db.test_results), rows)
    return IngestResult(run_pk=run_pk, inserted=len(rows))
