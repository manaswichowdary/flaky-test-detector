"""Import test history from public repos that upload JUnit XML as GitHub Actions artifacts.

Each (workflow run, artifact) becomes one stored run. Test ids are prefixed with the
artifact's environment label (e.g. "test-results-py3.12") so the same test on different
matrix legs is tracked separately; otherwise a test failing only on one Python version
would look flaky.
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
    sample_runs: int = 25
    artifact_pattern: str = DEFAULT_PATTERN
    max_artifact_mb: int = 200
    max_results_per_repo: int = 300_000


def load_config(path: str | Path = "history.toml") -> HistoryConfig:
    path = Path(path)
    if not path.exists():
        return HistoryConfig()
    with path.open("rb") as fh:
        data = tomllib.load(fh).get("history", {})
    return HistoryConfig(**data)


def env_label(artifact_name: str) -> str:
    """Drop run-specific parts (long numbers, commit shas) so the label is stable across runs."""
    label = re.sub(r"(?i)\b[0-9a-f]{7,40}\b|\d{6,}", "", artifact_name)
    return re.sub(r"[-_.]{2,}", "-", label).strip("-_. ") or artifact_name


def matching_artifacts(client: GitHubClient, repo: str, run_id: int, pattern: str) -> list[dict]:
    rx = re.compile(pattern)
    return [a for a in client.list_run_artifacts(repo, run_id)
            if rx.search(a["name"]) and not a.get("expired")]


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
    runs_checked: int = 0
    runs_with_junit: int = 0
    avg_mb: float = 0.0
    names: list[str] = field(default_factory=list)
    error: str | None = None

    def summary(self) -> str:
        if self.error:
            return f"{self.repo}: error {self.error}"
        return (f"{self.repo}: {self.runs_with_junit}/{self.runs_checked} recent runs have JUnit "
                f"artifacts, avg {self.avg_mb:.1f} MB/run, e.g. {', '.join(self.names[:3]) or '-'}")


def default_branch(client: GitHubClient, repo: str) -> str:
    return client.get(f"/repos/{repo}")["default_branch"]


def discover(client: GitHubClient, repo: str, pattern: str = DEFAULT_PATTERN,
             sample_runs: int = 25) -> DiscoverResult:
    out = DiscoverResult(repo)
    try:
        branch = default_branch(client, repo)
        runs = list(client.list_workflow_runs(repo, max_pages=1, status="completed",
                                              branch=branch, per_page=sample_runs))
        sizes, names = [], set()
        for run in runs:
            arts = matching_artifacts(client, repo, run["id"], pattern)
            out.runs_checked += 1
            if arts:
                out.runs_with_junit += 1
                sizes.append(sum(a["size_in_bytes"] for a in arts) / MB)
                names.update(a["name"] for a in arts)
        out.avg_mb = sum(sizes) / len(sizes) if sizes else 0.0
        out.names = sorted(names)
    except GitHubError as exc:
        out.error = str(exc)
    return out


@dataclass
class ImportResult:
    repo: str
    runs_imported: int = 0      # workflow runs with at least one JUnit artifact stored
    runs_already: int = 0
    runs_without_junit: int = 0
    stored_runs: int = 0        # (run, artifact) rows written
    results: int = 0
    artifacts_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.repo}: imported {self.runs_imported} workflow runs "
                f"({self.stored_runs} test runs, {self.results} test results); "
                f"{self.runs_already} already stored, {self.runs_without_junit} without JUnit, "
                f"{self.artifacts_skipped} artifacts skipped")


def import_repo(engine: Engine, client: GitHubClient, repo: str, cfg: HistoryConfig,
                scan_limit: int | None = None) -> ImportResult:
    out = ImportResult(repo)
    branch = default_branch(client, repo)
    scan_limit = scan_limit or cfg.max_runs * 5
    scanned = 0
    for run in client.list_workflow_runs(repo, max_pages=20, status="completed", branch=branch):
        if out.runs_imported + out.runs_already >= cfg.max_runs or scanned >= scan_limit:
            break
        if out.results >= cfg.max_results_per_repo:
            out.errors.append(f"stopped at max_results_per_repo={cfg.max_results_per_repo}")
            break
        scanned += 1
        arts = matching_artifacts(client, repo, run["id"], cfg.artifact_pattern)
        if not arts:
            out.runs_without_junit += 1
            continue
        attempt = int(run.get("run_attempt") or 1)
        keys = [f"{run['id']}/{env_label(a['name'])}" for a in arts]
        if all(run_exists(engine, repo, k, attempt) for k in keys):
            out.runs_already += 1
            continue
        stored_any = False
        for art, key in zip(arts, keys):
            if run_exists(engine, repo, key, attempt):
                continue
            if art["size_in_bytes"] > cfg.max_artifact_mb * MB:
                out.artifacts_skipped += 1
                continue
            try:
                results = extract_junit(client.download_artifact(repo, art["id"]))
            except (GitHubError, ValueError, zipfile.BadZipFile) as exc:
                out.artifacts_skipped += 1
                out.errors.append(f"{art['name']}: {exc}")
                continue
            if not results:
                out.artifacts_skipped += 1
                continue
            label = env_label(art["name"])
            for r in results:
                r.test_id = f"[{label}] {r.test_id}"
            meta = RunMeta(
                run_id=key, run_attempt=attempt, sha=run["head_sha"], branch=run.get("head_branch"),
                workflow=run.get("name"), source="history",
                started_at=parse_time(run.get("run_started_at")),
                completed_at=parse_time(run.get("updated_at")),
            )
            res = ingest(engine, repo, meta, results, bulk=True)
            out.stored_runs += 1
            out.results += res.inserted
            stored_any = True
        out.runs_imported += stored_any
    return out
