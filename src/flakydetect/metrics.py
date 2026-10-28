"""Numbers reported in the README, computed from the database."""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from . import db
from .junit import ERROR, FAILED, PASSED

BAD = (FAILED, ERROR)
_WORKFLOW_RUN = re.compile(r"^(\d+)")


def workflow_run_id(run_key: str) -> str:
    """'40757057762-demo-2' -> '40757057762' (stored run keys start with the Actions run id)."""
    m = _WORKFLOW_RUN.match(run_key)
    return m.group(1) if m else run_key


@dataclass
class Metrics:
    demo_repo: str
    planted_total: int
    planted_found: list[str]
    planted_missed: list[str]
    false_alarms: list[str]
    demo_workflow_runs: int
    failed_workflow_runs: int
    reruns_avoided: int
    real_failure_runs: int
    time_to_issue_minutes: list[float]
    test_runs: int
    workflow_runs: int
    repos: dict[str, int] = field(default_factory=dict)  # repo -> workflow runs
    generated_at: str = ""

    @property
    def median_minutes(self) -> float | None:
        return round(statistics.median(self.time_to_issue_minutes), 1) if self.time_to_issue_minutes else None

    @property
    def max_minutes(self) -> float | None:
        return round(max(self.time_to_issue_minutes), 1) if self.time_to_issue_minutes else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["median_minutes"], d["max_minutes"] = self.median_minutes, self.max_minutes
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Metrics":
        d = {k: v for k, v in d.items() if k not in ("median_minutes", "max_minutes")}
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def markdown(self) -> str:
        found = len(self.planted_found)
        tti = (f"median {fmt_minutes(self.median_minutes)}, max {fmt_minutes(self.max_minutes)} "
               f"({len(self.time_to_issue_minutes)} issues)" if self.time_to_issue_minutes else "no issues yet")
        repos = ", ".join(f"{r} {n}" for r, n in sorted(self.repos.items()))
        return "\n".join([
            "| Metric | Result |",
            "|---|---|",
            f"| Planted flaky tests found | **{found} of {self.planted_total}** |",
            f"| False alarms | **{len(self.false_alarms)}** |",
            f"| Reruns avoided on the demo repo | **{self.reruns_avoided} of {self.failed_workflow_runs}** "
            f"failed CI runs had only flaky failures |",
            f"| Time from a flaky failure to a quarantine issue | **{tti}** |",
            f"| Total CI test runs analyzed | **{self.test_runs:,}** test runs from "
            f"{self.workflow_runs:,} workflow runs ({repos}) |",
        ])


def fmt_minutes(m: float | None) -> str:
    if m is None:
        return "n/a"
    return f"{m:.1f} min" if m < 120 else f"{m / 60:.1f} h"


def compute(engine: Engine, demo_repo: str, planted: dict[str, str], demo_prefix: str = "demo.tests.",
            now: datetime | None = None) -> Metrics:
    with engine.connect() as conn:
        tests = {r.id: r for r in conn.execute(
            select(db.tests.c.id, db.tests.c.test_id, db.test_scores.c.is_flaky)
            .select_from(db.tests.outerjoin(db.test_scores)).where(db.tests.c.repo == demo_repo))}
        results = conn.execute(
            select(db.runs.c.run_id, db.runs.c.run_attempt, db.runs.c.sha, db.runs.c.completed_at,
                   db.test_results.c.test_pk, db.test_results.c.status, db.test_results.c.rerun_failures)
            .select_from(db.test_results.join(db.runs))
            .where(db.runs.c.repo == demo_repo, db.runs.c.source == "demo")
        ).all()
        issues = conn.execute(
            select(db.quarantine_issues.c.test_pk, db.quarantine_issues.c.created_at)
            .join(db.tests).where(db.tests.c.repo == demo_repo)).all()
        per_repo = defaultdict(set)
        test_runs = 0
        for repo, run_id, attempt in conn.execute(select(db.runs.c.repo, db.runs.c.run_id, db.runs.c.run_attempt)):
            per_repo[repo].add((workflow_run_id(run_id), attempt))
            test_runs += 1

    # 1. Planted tests found and false alarms.
    flagged = {t.test_id for t in tests.values() if t.is_flaky}
    keys = {f"{demo_prefix}{k}" for k in planted}
    found = sorted(k for k in planted if f"{demo_prefix}{k}" in flagged)

    # 2. Reruns avoided: failed demo workflow runs whose failures were all explained as
    #    flaky by evidence available when the run finished.
    by_wf: dict[tuple, list] = defaultdict(list)
    for r in results:
        by_wf[(workflow_run_id(r.run_id), r.run_attempt)].append(r)
    order = sorted(by_wf, key=lambda k: max(r.completed_at for r in by_wf[k]))
    evidence: set[int] = set()            # tests with flaky evidence so far
    seen: dict[tuple, set[str]] = {}      # (test, sha) -> statuses so far
    failed = avoided = real = 0
    for key in order:
        rows = by_wf[key]
        for r in rows:
            statuses = seen.setdefault((r.test_pk, r.sha), set())
            statuses.add("bad" if r.status in BAD else r.status)
            if r.status == PASSED and r.rerun_failures:
                evidence.add(r.test_pk)
            if {"bad", PASSED} <= statuses:
                evidence.add(r.test_pk)
        failing = {r.test_pk for r in rows if r.status in BAD}
        if failing:
            failed += 1
            if failing <= evidence:
                avoided += 1
            else:
                real += 1

    # 3. Time from the test's first failure to its quarantine issue.
    first_failure: dict[int, datetime] = {}
    for r in results:
        if r.status in BAD and (r.test_pk not in first_failure or r.completed_at < first_failure[r.test_pk]):
            first_failure[r.test_pk] = r.completed_at
    tti = sorted(
        round((i.created_at - first_failure[i.test_pk]).total_seconds() / 60, 1)
        for i in issues if i.test_pk in first_failure and i.created_at >= first_failure[i.test_pk]
    )

    return Metrics(
        demo_repo=demo_repo,
        planted_total=len(planted),
        planted_found=found,
        planted_missed=sorted(set(planted) - set(found)),
        false_alarms=sorted(flagged - keys),
        demo_workflow_runs=len(by_wf),
        failed_workflow_runs=failed,
        reruns_avoided=avoided,
        real_failure_runs=real,
        time_to_issue_minutes=tti,
        test_runs=test_runs,
        workflow_runs=sum(len(v) for v in per_repo.values()),
        repos={repo: len(v) for repo, v in per_repo.items()},
        generated_at=(now or db.utcnow()).strftime("%Y-%m-%d %H:%M UTC"),
    )


def readme_block(m: Metrics) -> str:
    return f"<!-- metrics:start -->\n{m.markdown()}\n\n<sub>Measured {m.generated_at} from the project database " \
           f"(`flakydetect metrics`); raw numbers in `docs/metrics.json`.</sub>\n<!-- metrics:end -->"
