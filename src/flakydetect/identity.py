"""Map raw test ids to a canonical test, following renames and moves (hard decision 3).

Strategies:
- none:      every distinct id is its own test.
- map:       explicit aliases from test_aliases.toml, otherwise exact ids.
- heuristic: map first, then link a brand-new id to a test that just disappeared when
             (a) both carry the same body fingerprint, or (b) the function name matches.
             A link is made only when exactly one candidate qualifies.
"""

from __future__ import annotations

import re
import tomllib
from datetime import datetime
from pathlib import Path

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from . import db
from .collector import Resolver, exact_resolver, get_or_create_test
from .config import Settings
from .junit import TestResult

FINGERPRINT = "fingerprint"


def load_alias_map(path: str | Path) -> dict[str, str]:
    """test_aliases.toml:  [aliases]\\n"new::id" = "old::id" """
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return dict(tomllib.load(fh).get("aliases", {}))


def function_name(test_id: str) -> str:
    """'pkg.mod.Class::test_x[param]' -> 'test_x[param]' (pytest and Java-style ids)."""
    tail = test_id.rsplit("::", 1)[-1]
    return re.sub(r"\(\)$", "", tail)


def _lookup(conn: Connection, repo: str, test_id: str) -> int | None:
    row = conn.execute(
        select(db.tests.c.id).where(db.tests.c.repo == repo, db.tests.c.test_id == test_id)
    ).first()
    if row:
        return row.id
    row = conn.execute(
        select(db.test_aliases.c.test_pk).where(
            db.test_aliases.c.repo == repo, db.test_aliases.c.alias == test_id
        )
    ).first()
    return row.test_pk if row else None


def _add_alias(conn: Connection, repo: str, alias: str, test_pk: int, reason: str) -> None:
    exists = conn.execute(
        select(db.test_aliases.c.id).where(
            db.test_aliases.c.repo == repo, db.test_aliases.c.alias == alias
        )
    ).first()
    if not exists:
        conn.execute(insert(db.test_aliases).values(
            repo=repo, alias=alias, test_pk=test_pk, reason=reason, created_at=db.utcnow()
        ))


def _rename(conn: Connection, repo: str, test_pk: int, new_id: str, reason: str,
            seen_at: datetime, fingerprint: str | None) -> int:
    """Point the canonical test at its new id; keep the old id as an alias."""
    old_id = conn.execute(select(db.tests.c.test_id).where(db.tests.c.id == test_pk)).scalar_one()
    values = {"test_id": new_id, "last_seen": seen_at}
    if fingerprint:
        values["fingerprint"] = fingerprint
    conn.execute(update(db.tests).where(db.tests.c.id == test_pk).values(**values))
    _add_alias(conn, repo, old_id, test_pk, reason)
    return test_pk


class IdentityResolver:
    def __init__(self, strategy: str, alias_map: dict[str, str] | None = None,
                 window_runs: int = 5):
        self.strategy = strategy
        self.alias_map = alias_map or {}
        self.window_runs = window_runs

    def __call__(self, conn: Connection, repo: str, result: TestResult, seen_at: datetime,
                 current_ids: frozenset[str] = frozenset()) -> int:
        if self.strategy == "none":
            return exact_resolver(conn, repo, result, seen_at)
        fingerprint = result.properties.get(FINGERPRINT)
        pk = _lookup(conn, repo, result.test_id)
        if pk is not None:
            conn.execute(update(db.tests).where(db.tests.c.id == pk).values(last_seen=seen_at))
            return pk
        mapped = self.alias_map.get(result.test_id)
        if mapped:
            old_pk = _lookup(conn, repo, mapped)
            if old_pk is not None:
                return _rename(conn, repo, old_pk, result.test_id, "map", seen_at, fingerprint)
        if self.strategy == "heuristic":
            match = self._find_renamed(conn, repo, result, current_ids)
            if match:
                old_pk, reason = match
                return _rename(conn, repo, old_pk, result.test_id, reason, seen_at, fingerprint)
        return get_or_create_test(conn, repo, result.test_id, seen_at, fingerprint)

    def _recent_cutoff(self, conn: Connection, repo: str) -> datetime | None:
        rows = conn.execute(
            select(db.runs.c.completed_at).where(db.runs.c.repo == repo)
            .order_by(db.runs.c.completed_at.desc()).limit(self.window_runs + 1)
        ).scalars().all()
        # The current run is already inserted; the oldest of the window is the cutoff.
        return rows[-1] if rows else None

    def _find_renamed(self, conn: Connection, repo: str, result: TestResult,
                      current_ids: frozenset[str]) -> tuple[int, str] | None:
        cutoff = self._recent_cutoff(conn, repo)
        query = select(db.tests.c.id, db.tests.c.test_id, db.tests.c.fingerprint).where(
            db.tests.c.repo == repo
        )
        if cutoff is not None:
            query = query.where(db.tests.c.last_seen >= cutoff)
        candidates = [
            row for row in conn.execute(query)
            if row.test_id not in current_ids and not self._alias_in_run(conn, repo, row.id, current_ids)
        ]
        fingerprint = result.properties.get(FINGERPRINT)
        if fingerprint:
            same_body = [c for c in candidates if c.fingerprint == fingerprint]
            if len(same_body) == 1:
                return same_body[0].id, "fingerprint"
        name = function_name(result.test_id)
        same_name = [c for c in candidates if function_name(c.test_id) == name]
        if len(same_name) == 1:
            other_fp = same_name[0].fingerprint
            # Same name but a different body is a different test that reused the name.
            if not (fingerprint and other_fp and other_fp != fingerprint):
                return same_name[0].id, "name"
        return None

    @staticmethod
    def _alias_in_run(conn: Connection, repo: str, test_pk: int, current_ids: frozenset[str]) -> bool:
        aliases = conn.execute(
            select(db.test_aliases.c.alias).where(
                db.test_aliases.c.repo == repo, db.test_aliases.c.test_pk == test_pk
            )
        ).scalars()
        return any(a in current_ids for a in aliases)


def make_resolver(settings: Settings) -> Resolver:
    alias_map = load_alias_map(settings.aliases_file) if settings.rename_strategy != "none" else {}
    return IdentityResolver(settings.rename_strategy, alias_map, settings.rename_window_runs)
