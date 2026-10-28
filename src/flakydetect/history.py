"""Import test history from public repos that upload JUnit XML as GitHub Actions artifacts.

Each (workflow run, environment) becomes one stored run, where the environment is the
artifact name minus run ids and shard numbers (e.g. "test-results-py3.12"). Test ids are
prefixed with it so the same test on different matrix legs is tracked separately;
otherwise a test failing only on one Python version would look flaky.
"""

from __future__ import annotations

import io
import re
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import Engine

from . import junit
from .collector import RunMeta, ingest, run_exists
from .github_api import GitHubClient, GitHubError, parse_time

DEFAULT_PATTERN = r"(?i)(junit|test-?results?|test-?reports?|surefire)"
MB = 1024 * 1024


@dataclass
class HistoryConfig:
    candidates: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)
    max_runs: int = 30
    sample_pages: int = 3
    artifact_pattern: str = DEFAULT_PATTERN
    max_artifact_mb: int = 200
    max_results_per_repo: int = 300_000
    repo_patterns: dict[str, str] = field(default_factory=dict)  # per-repo artifact_pattern

    def pattern_for(self, repo: str) -> str:
        return self.repo_patterns.get(repo, self.artifact_pattern)


def load_config(path: str | Path = "history.toml") -> HistoryConfig:
    path = Path(path)
    if not path.exists():
        return HistoryConfig()
    with path.open("rb") as fh:
        data = tomllib.load(fh).get("history", {})
    return HistoryConfig(**data)


def env_label(artifact_name: str) -> str:
    """Stable environment label for an artifact: drop run-specific parts (long numbers,
    commit shas) and a trailing shard index ("-3"), since suites rebalance tests across
    shards between runs."""
    label = re.sub(r"(?i)\b[0-9a-f]{7,40}\b|\d{6,}", "", artifact_name)
    label = re.sub(r"[-_.]{2,}", "-", label).strip("-_. ")
    label = re.sub(r"[-_](\d{1,3})$", "", label)
    return label or artifact_name


TEST_WORKFLOW = re.compile(r"(?i)test|\bci\b|build|check")


def junit_runs(client: GitHubClient, repo: str, pattern: str, branch: str,
               max_pages: int, max_runs: int) -> tuple[dict[int, list[dict]], str]:
    """Unexpired JUnit-looking artifacts on `branch`, grouped by workflow run (newest first).

    Tries the repo-wide artifacts listing first (100 artifacts per call, any workflow). Large
    repos make that endpoint time out with HTTP 5xx, so it falls back to scanning the runs of
    workflows that look like test workflows. Returns (runs, method used)."""
    rx = re.compile(pattern)
    try:
        runs: dict[int, list[dict]] = {}
        for art in client.list_repo_artifacts(repo, max_pages=max_pages):
            run = art.get("workflow_run") or {}
            if art.get("expired") or not rx.search(art["name"]) or run.get("head_branch") != branch:
                continue
            if run["id"] not in runs and len(runs) >= max_runs:
                break
            runs.setdefault(run["id"], []).append(art)
        return runs, "repo artifacts"
    except GitHubError as exc:
        if exc.status < 500:
            raise
    return _junit_runs_by_workflow(client, repo, rx, branch, max_runs)


def _junit_runs_by_workflow(client: GitHubClient, repo: str, rx: re.Pattern, branch: str,
                            max_runs: int, max_workflows: int = 4) -> tuple[dict[int, list[dict]], str]:
    workflows = client.get(f"/repos/{repo}/actions/workflows", per_page=100)["workflows"]
    candidates = [w for w in workflows
                  if w.get("state") == "active" and TEST_WORKFLOW.search(f"{w['name']} {w['path']}")]
    # Workflows named like tests first.
    candidates.sort(key=lambda w: "test" not in w["name"].lower())
    runs: dict[int, list[dict]] = {}
    used = []
    for wf in candidates[:max_workflows]:
        found = 0
        for run in client.paginate(f"/repos/{repo}/actions/workflows/{wf['id']}/runs",
                                   key="workflow_runs", max_pages=1, branch=branch,
                                   status="completed", per_page=min(max_runs * 2, 100)):
            arts = [a for a in client.list_run_artifacts(repo, run["id"])
                    if rx.search(a["name"]) and not a.get("expired")]
            if arts:
                runs[run["id"]] = arts
                found += 1
                if len(runs) >= max_runs:
                    break
        if found:
            used.append(wf["name"])
        if len(runs) >= max_runs:
            break
    return runs, f"workflows: {', '.join(used) or 'none matched'}"


def extract_junit(blob: bytes, max_entries: int = 5000, max_uncompressed_mb: int = 1024) -> list[junit.TestResult]:
    """JUnit results from every *.xml in a zip; other XML (coverage, etc.) is skipped."""
    results: list[junit.TestResult] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        infos = [i for i in zf.infolist() if i.filename.lower().endswith(".xml") and not i.is_dir()]
        if len(infos) > max_entries:
            raise ValueError(f"artifact has {len(infos)} XML files (limit {max_entries})")
        if sum(i.file_size for i in infos) > max_uncompressed_mb * MB:
            raise ValueError("artifact too large when uncompressed")
        for info in infos:
            try:
                results.extend(junit.parse_string(zf.read(info)))
            except junit.JUnitParseError:
                continue
    return results


@dataclass
class DiscoverResult:
    repo: str
    branch: str = ""
    method: str = ""
    runs_with_junit: int = 0
    avg_mb: float = 0.0
    names: list[str] = field(default_factory=list)
    error: str | None = None

    def summary(self) -> str:
        if self.error:
            return f"{self.repo}: error {self.error}"
        return (f"{self.repo}: {self.runs_with_junit} runs on {self.branch} with JUnit artifacts, "
                f"avg {self.avg_mb:.1f} MB/run, e.g. {', '.join(self.names[:3]) or '-'} "
                f"[{self.method}]")


def default_branch(client: GitHubClient, repo: str) -> str:
    return client.get(f"/repos/{repo}")["default_branch"]


def discover(client: GitHubClient, repo: str, pattern: str = DEFAULT_PATTERN,
             sample_pages: int = 3, sample_runs: int = 10) -> DiscoverResult:
    out = DiscoverResult(repo)
    try:
        out.branch = default_branch(client, repo)
        runs, out.method = junit_runs(client, repo, pattern, out.branch, sample_pages, sample_runs)
        out.runs_with_junit = len(runs)
        sizes = [sum(a["size_in_bytes"] for a in arts) / MB for arts in runs.values()]
        out.avg_mb = sum(sizes) / len(sizes) if sizes else 0.0
        out.names = sorted({env_label(a["name"]) for arts in runs.values() for a in arts})
    except GitHubError as exc:
        out.error = str(exc)
    return out


@dataclass
class ImportResult:
    repo: str
    runs_imported: int = 0      # workflow runs with at least one JUnit artifact stored
    runs_already: int = 0
    stored_runs: int = 0        # (run, artifact env) rows written
    results: int = 0
    artifacts_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.repo}: imported {self.runs_imported} workflow runs "
                f"({self.stored_runs} test runs, {self.results} test results); "
                f"{self.runs_already} already stored, {self.artifacts_skipped} artifacts skipped")


def _group_by_env(arts: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for art in arts:
        groups.setdefault(env_label(art["name"]), []).append(art)
    return groups


def import_repo(engine: Engine, client: GitHubClient, repo: str, cfg: HistoryConfig,
                max_pages: int = 30) -> ImportResult:
    """Import the newest `cfg.max_runs` default-branch runs that have JUnit artifacts.
    All shards of one environment in a run are stored together as one test run."""
    out = ImportResult(repo)
    branch = default_branch(client, repo)
    runs, _ = junit_runs(client, repo, cfg.pattern_for(repo), branch, max_pages, cfg.max_runs)
    for run_id, arts in runs.items():
        if out.results >= cfg.max_results_per_repo:
            out.errors.append(f"stopped at max_results_per_repo={cfg.max_results_per_repo}")
            break
        envs = _group_by_env(arts)
        run = client.get_run(repo, run_id)
        attempt = int(run.get("run_attempt") or 1)
        keys = {label: f"{run_id}/{label}" for label in envs}
        if all(run_exists(engine, repo, k, attempt) for k in keys.values()):
            out.runs_already += 1
            continue
        stored_any = False
        for label, group in envs.items():
            if run_exists(engine, repo, keys[label], attempt):
                continue
            results: list[junit.TestResult] = []
            for art in group:
                if art["size_in_bytes"] > cfg.max_artifact_mb * MB:
                    out.artifacts_skipped += 1
                    continue
                try:
                    results.extend(extract_junit(client.download_artifact(repo, art["id"])))
                except (GitHubError, ValueError, zipfile.BadZipFile) as exc:
                    out.artifacts_skipped += 1
                    out.errors.append(f"{art['name']}: {exc}")
            if not results:
                continue
            for r in results:
                r.test_id = f"[{label}] {r.test_id}"
            meta = RunMeta(
                run_id=keys[label], run_attempt=attempt, sha=run["head_sha"],
                branch=run.get("head_branch"), workflow=run.get("name"), source="history",
                started_at=parse_time(run.get("run_started_at")),
                completed_at=parse_time(run.get("updated_at")),
            )
            res = ingest(engine, repo, meta, results, bulk=True)
            out.stored_runs += 1
            out.results += res.inserted
            stored_any = True
        out.runs_imported += stored_any
    return out
