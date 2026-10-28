"""PR comments and quarantine issues (hard decision 2: warn | issue | auto)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from . import db
from .config import Settings
from .github_api import GitHubClient, parse_time
from .scorer import FailureVerdict, classify_run_failures

COMMENT_MARKER = "<!-- flakydetect:pr-comment -->"


def issue_marker(test_id: str) -> str:
    return f"<!-- flakydetect:test {test_id} -->"


@dataclass
class NotifyResult:
    verdicts: list[FailureVerdict] = field(default_factory=list)
    comment_id: int | None = None
    issues_opened: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)


def sha_verdicts(engine: Engine, repo: str, sha: str) -> list[FailureVerdict]:
    """Failures across every run of this commit (matrix jobs, reruns), one verdict per test."""
    with engine.connect() as conn:
        run_pks = conn.execute(
            select(db.runs.c.id).where(db.runs.c.repo == repo, db.runs.c.sha == sha)
            .order_by(db.runs.c.id)
        ).scalars().all()
    merged: dict[int, FailureVerdict] = {}
    for pk in run_pks:
        for v in classify_run_failures(engine, pk):
            prev = merged.get(v.test_pk)
            if prev is None or (v.flaky and not prev.flaky):
                merged[v.test_pk] = v
    return sorted(merged.values(), key=lambda v: (not v.flaky, v.test_id))


def build_comment(verdicts: list[FailureVerdict], sha: str, issues: dict[str, str],
                  mode: str) -> str:
    lines = [COMMENT_MARKER, f"### Flaky test check for `{sha[:7]}`", ""]
    if not verdicts:
        lines.append("All failing tests from earlier runs now pass. Nothing to report.")
        return "\n".join(lines)
    flaky = [v for v in verdicts if v.flaky]
    real = [v for v in verdicts if not v.flaky]
    if flaky:
        lines += [f"**{len(flaky)} failure(s) look flaky.** Rerunning is unlikely to tell you "
                  "anything new about your change.", "",
                  "| Test | Score | Why | Issue |", "|---|---|---|---|"]
        for v in flaky:
            issue = issues.get(v.test_id, "")
            lines.append(f"| `{v.test_id}` | {v.score:.2f} | {v.reason} | {issue} |")
        lines.append("")
    if real:
        lines += [f"**{len(real)} failure(s) look real.** These have no flaky history:", ""]
        lines += [f"- `{v.test_id}`: {(v.message or v.status)[:120]}" for v in real]
        lines.append("")
    lines.append(f"<sub>mode: `{mode}` · flakydetect</sub>")
    return "\n".join(lines)


def build_issue(test_id: str, score_row, verdict: FailureVerdict | None) -> tuple[str, str]:
    title = f"Quarantine flaky test: {test_id}"
    body = "\n".join([
        issue_marker(test_id),
        f"`{test_id}` passes and fails on the same code.",
        "",
        f"- Flakiness score: **{score_row.score:.2f}**",
        f"- Commits with both pass and fail: {score_row.mixed_commits}",
        f"- Passed only on rerun: {score_row.rerun_passes}",
        f"- Runs observed: {score_row.runs_seen} across {score_row.commits_seen} commits",
        f"- Latest failure: {(verdict.message if verdict else None) or 'n/a'}",
        "",
        "Suggested next steps: find the root cause (timing, shared state, test order, network, "
        "random data, async race), fix it, then close this issue. Closing the issue lifts the "
        "quarantine in `auto` mode.",
    ])
    return title, body


def _find_comment(client: GitHubClient, repo: str, pr: int) -> dict | None:
    return next((c for c in client.list_issue_comments(repo, pr)
                 if COMMENT_MARKER in (c.get("body") or "")), None)


def _sync_issue_states(engine: Engine, client: GitHubClient, repo: str) -> dict[int, dict]:
    """Return open quarantine issues by test_pk; forget ones that were closed (= fixed)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.quarantine_issues).join(db.tests)
            .where(db.tests.c.repo == repo, db.quarantine_issues.c.state == "open")
        ).mappings().all()
    open_rows = {}
    for row in rows:
        state = client.get_issue(repo, row["issue_number"]).get("state", "open")
        if state == "open":
            open_rows[row["test_pk"]] = dict(row)
        else:
            with engine.begin() as conn:
                conn.execute(update(db.quarantine_issues)
                             .where(db.quarantine_issues.c.test_pk == row["test_pk"])
                             .values(state="closed"))
    return open_rows


def _existing_issue_on_github(client: GitHubClient, repo: str, test_id: str, label: str) -> dict | None:
    marker = issue_marker(test_id)
    for issue in client.list_issues(repo, labels=label, state="open"):
        if marker in (issue.get("body") or ""):
            return issue
    return None


def write_quarantine_file(path: str | Path, test_ids: set[str]) -> None:
    path = Path(path)
    existing = set(path.read_text().split()) if path.exists() else set()
    path.write_text("".join(f"{t}\n" for t in sorted(existing | test_ids)))


def quarantined_tests(engine: Engine, repo: str) -> list[str]:
    with engine.connect() as conn:
        return sorted(conn.execute(
            select(db.tests.c.test_id).join(db.quarantine_issues)
            .where(db.tests.c.repo == repo, db.quarantine_issues.c.state == "open")
        ).scalars())


def notify(engine: Engine, client: GitHubClient, settings: Settings, repo: str, sha: str,
           pr_number: int | None) -> NotifyResult:
    result = NotifyResult(verdicts=sha_verdicts(engine, repo, sha))
    issue_links: dict[str, str] = {}

    if settings.mode in ("issue", "auto"):
        open_issues = _sync_issue_states(engine, client, repo)
        with engine.connect() as conn:
            scores = {
                r.test_pk: r for r in conn.execute(
                    select(db.test_scores).where(db.test_scores.c.is_flaky.is_(True))
                )
            }
        for v in result.verdicts:
            score_row = scores.get(v.test_pk)
            if v.test_pk in open_issues:
                issue_links[v.test_id] = f"#{open_issues[v.test_pk]['issue_number']}"
                continue
            if score_row is None:
                continue  # flaky on this commit only; not enough evidence to quarantine yet
            issue = _existing_issue_on_github(client, repo, v.test_id, settings.issue_label)
            if issue is None:
                title, body = build_issue(v.test_id, score_row, v)
                issue = client.create_issue(repo, title, body, [settings.issue_label])
                result.issues_opened.append(v.test_id)
            with engine.begin() as conn:
                conn.execute(delete(db.quarantine_issues)
                             .where(db.quarantine_issues.c.test_pk == v.test_pk))
                conn.execute(insert(db.quarantine_issues).values(
                    test_pk=v.test_pk, issue_number=issue["number"], issue_url=issue["html_url"],
                    state="open", failure_at=score_row.last_failure_at,
                    created_at=parse_time(issue.get("created_at")) or db.utcnow(),
                ))
            issue_links[v.test_id] = f"#{issue['number']}"

    if settings.mode == "auto":
        result.quarantined = quarantined_tests(engine, repo)
        write_quarantine_file(settings.quarantine_file, set(result.quarantined))

    if pr_number is not None:
        body = build_comment(result.verdicts, sha, issue_links, settings.mode)
        existing = _find_comment(client, repo, pr_number)
        if existing is not None:
            if existing["body"] != body:
                client.update_comment(repo, existing["id"], body)
            result.comment_id = existing["id"]
        elif result.verdicts:
            result.comment_id = client.create_comment(repo, pr_number, body)["id"]
    return result
