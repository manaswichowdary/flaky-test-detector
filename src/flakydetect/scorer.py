"""Flakiness score (hard decision 1).

Evidence is collected per commit inside the window:
- mixed commit: the same sha has at least one pass and one failure (the code didn't change);
- rerun pass:   a failure that passed on retry, either inside one run (Surefire
                flakyFailure / pytest-rerunfailures) or on a later run attempt.

score = sum(weight of commits with evidence) / sum(weight of all commits seen), where a
commit's weight halves every `half_life_days`. A test is flaky when it has enough evidence
(min_mixed_commits or min_rerun_passes), score >= min_score, and it hasn't since passed
`clear_after_clean_commits` commits in a row. A test that always fails is broken, not flaky.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine

from . import db
from .config import Settings
from .junit import ERROR, FAILED, PASSED

BAD = (FAILED, ERROR)


@dataclass
class Score:
    test_pk: int
    test_id: str
    score: float
    mixed_commits: int
    rerun_passes: int
    commits_seen: int
    runs_seen: int
    failures: int
    is_flaky: bool
    last_failure_at: datetime | None


@dataclass
class _Row:
    run_id: str
    attempt: int
    sha: str
    at: datetime
    status: str
    rerun_failures: int


def compute(rows: list[_Row], settings: Settings, now: datetime) -> dict:
    by_sha: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        if r.status in (PASSED, *BAD):
            by_sha[r.sha].append(r)

    rerun_passes = sum(1 for r in rows if r.status == PASSED and r.rerun_failures > 0)
    # Failed on attempt N, passed on a later attempt of the same run.
    by_run: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        by_run[r.run_id].append(r)
    for attempts in by_run.values():
        attempts.sort(key=lambda r: r.attempt)
        for prev, nxt in zip(attempts, attempts[1:]):
            if prev.status in BAD and nxt.status == PASSED and nxt.attempt > prev.attempt:
                rerun_passes += 1

    mixed = 0
    weighted_all = weighted_evidence = 0.0
    commits = []  # (latest time, has_evidence)
    for sha_rows in by_sha.values():
        statuses = {r.status for r in sha_rows}
        is_mixed = PASSED in statuses and bool(statuses & set(BAD))
        has_rerun = any(r.status == PASSED and r.rerun_failures > 0 for r in sha_rows)
        mixed += is_mixed
        latest = max(r.at for r in sha_rows)
        age_days = max((now - latest).total_seconds() / 86400, 0.0)
        weight = 0.5 ** (age_days / settings.half_life_days)
        weighted_all += weight
        evidence = is_mixed or has_rerun
        weighted_evidence += weight * evidence
        commits.append((latest, evidence))

    score = weighted_evidence / weighted_all if weighted_all else 0.0
    commits.sort()
    clean_streak = 0
    for _, evidence in reversed(commits):
        if evidence:
            break
        clean_streak += 1
    enough = mixed >= settings.min_mixed_commits or rerun_passes >= settings.min_rerun_passes
    failures = [r.at for r in rows if r.status in BAD or r.rerun_failures > 0]
    return {
        "score": round(score, 4),
        "mixed_commits": mixed,
        "rerun_passes": rerun_passes,
        "commits_seen": len(by_sha),
        "runs_seen": len(rows),
        "failures": sum(1 for r in rows if r.status in BAD),
        "is_flaky": bool(enough and score >= settings.min_score
                         and clean_streak < settings.clear_after_clean_commits),
        "last_failure_at": max(failures) if failures else None,
    }


def score_repo(engine: Engine, repo: str, settings: Settings, now: datetime | None = None) -> list[Score]:
    now = now or db.utcnow()
    since = now - timedelta(days=settings.window_days)
    query = (
        select(db.tests.c.id, db.tests.c.test_id, db.runs.c.run_id, db.runs.c.run_attempt,
               db.runs.c.sha, db.runs.c.completed_at, db.test_results.c.status,
               db.test_results.c.rerun_failures)
        .select_from(db.test_results.join(db.runs).join(db.tests))
        .where(db.tests.c.repo == repo, db.runs.c.completed_at >= since)
    )
    per_test: dict[tuple[int, str], list[_Row]] = defaultdict(list)
    with engine.connect() as conn:
        all_tests = conn.execute(
            select(db.tests.c.id, db.tests.c.test_id).where(db.tests.c.repo == repo)
        ).all()
        for row in conn.execute(query):
            per_test[(row.id, row.test_id)].append(
                _Row(row.run_id, row.run_attempt, row.sha, row.completed_at, row.status, row.rerun_failures)
            )
    scores = [
        Score(test_pk=pk, test_id=tid, **compute(per_test.get((pk, tid), []), settings, now))
        for pk, tid in all_tests
    ]
    with engine.begin() as conn:
        pks = [s.test_pk for s in scores]
        if pks:
            conn.execute(delete(db.test_scores).where(db.test_scores.c.test_pk.in_(pks)))
            conn.execute(insert(db.test_scores), [
                {k: v for k, v in vars(s).items() if k != "test_id"} | {"updated_at": now}
                for s in scores
            ])
    return scores


@dataclass
class FailureVerdict:
    test_pk: int
    test_id: str
    status: str
    message: str | None
    flaky: bool
    score: float
    reason: str


def classify_run_failures(engine: Engine, run_pk: int) -> list[FailureVerdict]:
    """For each failing test in a run: is it probably flaky? Uses the stored scores plus
    direct evidence from the same commit (another run of this sha passed)."""
    with engine.connect() as conn:
        run = conn.execute(select(db.runs).where(db.runs.c.id == run_pk)).one()
        failing = conn.execute(
            select(db.test_results.c.test_pk, db.test_results.c.status, db.test_results.c.message,
                   db.tests.c.test_id, db.test_scores.c.score, db.test_scores.c.is_flaky)
            .select_from(db.test_results.join(db.tests).outerjoin(db.test_scores))
            .where(db.test_results.c.run_pk == run_pk, db.test_results.c.status.in_(BAD))
        ).all()
        verdicts = []
        for f in failing:
            passed_same_sha = conn.execute(
                select(db.test_results.c.id).select_from(db.test_results.join(db.runs))
                .where(db.runs.c.repo == run.repo, db.runs.c.sha == run.sha,
                       db.test_results.c.test_pk == f.test_pk,
                       db.test_results.c.status == PASSED)
                .limit(1)
            ).first()
            if f.is_flaky:
                reason = f"known flaky (score {f.score:.2f})"
            elif passed_same_sha:
                reason = "passed on another run of this same commit"
            else:
                reason = "no flaky history; likely a real failure"
            verdicts.append(FailureVerdict(
                test_pk=f.test_pk, test_id=f.test_id, status=f.status, message=f.message,
                flaky=bool(f.is_flaky or passed_same_sha), score=f.score or 0.0, reason=reason,
            ))
    return sorted(verdicts, key=lambda v: (not v.flaky, v.test_id))
