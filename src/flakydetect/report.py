"""Static HTML report of the flakiest tests (nightly)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from . import db

RECENT = 20  # outcomes shown per test


@dataclass
class Row:
    repo: str
    test_id: str
    score: float
    is_flaky: bool
    mixed_commits: int
    rerun_passes: int
    runs_seen: int
    failures: int
    last_failure_at: datetime | None
    issue_number: int | None = None
    issue_url: str | None = None
    recent: list[str] = field(default_factory=list)  # oldest -> newest: "p", "f", "s"


@dataclass
class RepoSummary:
    repo: str
    tests: int
    runs: int
    flaky: int


@dataclass
class ReportData:
    generated_at: datetime
    rows: list[Row]
    repos: list[RepoSummary]

    @property
    def total_runs(self) -> int:
        return sum(r.runs for r in self.repos)


def _recent_outcomes(engine: Engine, test_pks: list[int]) -> dict[int, list[str]]:
    if not test_pks:
        return {}
    out: dict[int, list[tuple]] = defaultdict(list)
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.test_results.c.test_pk, db.test_results.c.status,
                   db.test_results.c.rerun_failures, db.runs.c.completed_at, db.runs.c.id)
            .join(db.runs).where(db.test_results.c.test_pk.in_(test_pks))
        )
        for r in rows:
            mark = {"passed": "r" if r.rerun_failures else "p", "skipped": "s"}.get(r.status, "f")
            out[r.test_pk].append((r.completed_at, r.id, mark))
    return {pk: [m for *_, m in sorted(v)[-RECENT:]] for pk, v in out.items()}


def collect(engine: Engine, limit: int = 200, now: datetime | None = None) -> ReportData:
    """Tests with any flaky evidence or failure, flakiest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.tests.c.repo, db.tests.c.test_id, db.test_scores,
                   db.quarantine_issues.c.issue_number, db.quarantine_issues.c.issue_url,
                   db.quarantine_issues.c.state)
            .select_from(db.test_scores.join(db.tests).outerjoin(db.quarantine_issues))
            .where((db.test_scores.c.score > 0) | (db.test_scores.c.failures > 0))
            .order_by(db.test_scores.c.score.desc(), db.test_scores.c.failures.desc(),
                      db.tests.c.test_id)
            .limit(limit)
        ).all()
        repos = []
        for repo, tests in conn.execute(
            select(db.tests.c.repo, func.count()).group_by(db.tests.c.repo).order_by(db.tests.c.repo)
        ):
            runs = conn.execute(select(func.count()).select_from(db.runs)
                                .where(db.runs.c.repo == repo)).scalar_one()
            flaky = conn.execute(
                select(func.count()).select_from(db.test_scores.join(db.tests))
                .where(db.tests.c.repo == repo, db.test_scores.c.is_flaky.is_(True))
            ).scalar_one()
            repos.append(RepoSummary(repo, tests, runs, flaky))
    recent = _recent_outcomes(engine, [r.test_pk for r in rows])
    return ReportData(
        generated_at=now or db.utcnow(),
        repos=repos,
        rows=[Row(
            repo=r.repo, test_id=r.test_id, score=r.score, is_flaky=r.is_flaky,
            mixed_commits=r.mixed_commits, rerun_passes=r.rerun_passes, runs_seen=r.runs_seen,
            failures=r.failures, last_failure_at=r.last_failure_at,
            issue_number=r.issue_number if r.state == "open" else None,
            issue_url=r.issue_url if r.state == "open" else None,
            recent=recent.get(r.test_pk, []),
        ) for r in rows],
    )


def render(data: ReportData) -> str:
    env = Environment(loader=PackageLoader("flakydetect", "templates"),
                      autoescape=select_autoescape(["html"]))
    return env.get_template("report.html").render(data=data)


def write(engine: Engine, out_dir: str | Path, limit: int = 200) -> tuple[Path, ReportData]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = collect(engine, limit)
    path = out / "index.html"
    path.write_text(render(data), encoding="utf-8")
    return path, data
