import io
import zipfile

import pytest
import responses

from flakydetect import cli
from flakydetect.github_api import GitHubClient
from flakydetect.history import (
    HistoryConfig, discover, env_label, extract_junit, import_repo, load_config,
)

API = "https://api.github.com"
REPO = "up/stream"

XML_A = b'<testsuite><testcase classname="pkg.T" name="a"/><testcase classname="pkg.T" name="b"><failure message="x"/></testcase></testsuite>'
XML_B = b'<testsuites><testsuite><testcase classname="pkg.U" name="c"/></testsuite></testsuites>'
COVERAGE = b'<coverage line-rate="0.9"/>'


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    return GitHubClient(token="t", sleep=lambda s: None)


def mock_repo(runs, artifacts, blobs):
    """runs: list of run dicts; artifacts: list of artifact dicts (newest first)."""
    responses.get(f"{API}/repos/{REPO}", json={"default_branch": "main"})
    responses.get(f"{API}/repos/{REPO}/actions/artifacts", json={"artifacts": artifacts})
    for r in runs:
        responses.get(f"{API}/repos/{REPO}/actions/runs/{r['id']}", json=r)
    for art_id, blob in blobs.items():
        responses.get(f"{API}/repos/{REPO}/actions/artifacts/{art_id}/zip", body=blob)


def run(i, sha="s"):
    return {"id": i, "run_attempt": 1, "head_sha": f"{sha}{i}", "head_branch": "main",
            "name": "CI", "updated_at": "2026-10-20T10:00:00Z", "run_started_at": "2026-10-20T09:50:00Z"}


def art(i, run_id, name="test-results-py3.12", size=1000, expired=False, branch="main"):
    return {"id": i, "name": name, "size_in_bytes": size, "expired": expired,
            "workflow_run": {"id": run_id, "head_branch": branch}}


def test_extract_junit_skips_non_junit_and_junk():
    blob = make_zip({"a/junit.xml": XML_A, "nested/deep/b.xml": XML_B,
                     "coverage.xml": COVERAGE, "log.txt": b"hi", "bad.xml": b"<oops"})
    assert sorted(r.test_id for r in extract_junit(blob)) == ["pkg.T::a", "pkg.T::b", "pkg.U::c"]


def test_extract_junit_entry_limit():
    with pytest.raises(ValueError, match="limit"):
        extract_junit(make_zip({f"{i}.xml": XML_B for i in range(3)}), max_entries=2)


@pytest.mark.parametrize("name,label", [
    ("test-results-py3.12", "test-results-py3.12"),
    ("junit-1234567890-ubuntu", "junit-ubuntu"),
    ("surefire-reports-3f2a9c1d", "surefire-reports"),
    ("test-results-full-3.14.10-7", "test-results-full-3.14.10"),
    ("unit-test-reports-jdk25-000320bd", "unit-test-reports-jdk25"),
])
def test_env_label(name, label):
    assert env_label(name) == label


@responses.activate
def test_import_filters_and_is_resumable(engine, client, count):
    arts = [
        art(30, 3, name="junit-ubuntu"), art(31, 3, name="junit-macos"),
        art(20, 2, expired=True),
        art(11, 1, name="coverage-report"), art(10, 1),
        art(40, 4, branch="feature-x"),
    ]
    mock_repo([run(1), run(2), run(3), run(4)], arts,
              {10: make_zip({"r.xml": XML_A}), 30: make_zip({"r.xml": XML_A}),
               31: make_zip({"r.xml": XML_B})})
    cfg = HistoryConfig(max_runs=10)
    out = import_repo(engine, client, REPO, cfg)
    assert (out.runs_imported, out.stored_runs, out.results) == (2, 3, 5)
    assert count("runs") == 3 and count("test_results") == 5
    assert count("tests") == 5  # macOS and ubuntu legs are separate tests
    downloads = sum("/zip" in c.request.url for c in responses.calls)
    again = import_repo(engine, client, REPO, cfg)
    assert again.runs_already == 2 and again.results == 0
    assert sum("/zip" in c.request.url for c in responses.calls) == downloads  # no re-download


@responses.activate
def test_shards_of_one_env_become_one_test_run(engine, client, count):
    arts = [art(1, 9, name="test-results-full-3.14-1"), art(2, 9, name="test-results-full-3.14-2")]
    mock_repo([run(9)], arts, {1: make_zip({"a.xml": XML_A}), 2: make_zip({"b.xml": XML_B})})
    out = import_repo(engine, client, REPO, HistoryConfig())
    assert (out.stored_runs, out.results) == (1, 3)
    from sqlalchemy import select

    from flakydetect import db
    with engine.connect() as conn:
        ids = set(conn.execute(select(db.tests.c.test_id)).scalars())
    assert "[test-results-full-3.14] pkg.U::c" in ids


@responses.activate
def test_import_respects_size_and_max_runs(engine, client):
    mock_repo([run(1), run(2)], [art(20, 2), art(10, 1, size=500 * 1024 * 1024)],
              {20: make_zip({"r.xml": XML_B})})
    out = import_repo(engine, client, REPO, HistoryConfig(max_runs=10, max_artifact_mb=100))
    assert out.artifacts_skipped == 1 and out.runs_imported == 1


@responses.activate
def test_import_max_runs_takes_newest(engine, client):
    mock_repo([run(1), run(2)], [art(20, 2), art(10, 1)],
              {20: make_zip({"r.xml": XML_B}), 10: make_zip({"r.xml": XML_A})})
    out = import_repo(engine, client, REPO, HistoryConfig(max_runs=1))
    assert out.runs_imported == 1 and out.results == 1


@responses.activate
def test_import_records_bad_zip(engine, client):
    mock_repo([run(1)], [art(10, 1)], {10: b"not a zip"})
    out = import_repo(engine, client, REPO, HistoryConfig())
    assert out.artifacts_skipped == 1 and "test-results" in out.errors[0]


@responses.activate
def test_discover_summary(client):
    mock_repo([], [art(10, 1, size=2 * 1024 * 1024), art(11, 1, name="junit-b"),
                   art(12, 2, branch="other"), art(13, 3, name="dist")], {})
    res = discover(client, REPO)
    assert (res.runs_with_junit, res.branch) == (1, "main")
    assert "1 runs on main" in res.summary()


@responses.activate
def test_discover_reports_errors(client):
    responses.get(f"{API}/repos/{REPO}", status=404, json={"message": "Not Found"})
    assert "error" in discover(client, REPO).summary()


def test_load_config(tmp_path):
    f = tmp_path / "h.toml"
    f.write_text('[history]\ncandidates = ["a/b"]\nrepos = ["a/b"]\nmax_runs = 5\n')
    cfg = load_config(f)
    assert cfg.repos == ["a/b"] and cfg.max_runs == 5
    assert load_config(tmp_path / "missing.toml") == HistoryConfig()


def test_repo_history_toml_is_valid():
    cfg = load_config("history.toml")
    assert len(cfg.candidates) >= 2 and cfg.max_results_per_repo > 0
    assert len(cfg.repos) == 2
    assert cfg.max_results_per_repo == 100_000 and cfg.max_db_mb == 300
    assert cfg.pattern_for("home-assistant/core") == "^test-results-full-"
    assert cfg.pattern_for("other/repo") == cfg.artifact_pattern


def test_import_history_cli_noop_without_repos(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "history.toml").write_text("[history]\nrepos = []\n")
    assert cli.main(["import-history"]) == 0
    assert "nothing to import" in capsys.readouterr().out


def mock_big_repo(runs, run_artifacts, blobs):
    """A repo whose repo-wide artifacts listing times out (HTTP 500)."""
    responses.get(f"{API}/repos/{REPO}", json={"default_branch": "main"})
    responses.get(f"{API}/repos/{REPO}/actions/artifacts", status=500, json={"message": ""})
    responses.get(f"{API}/repos/{REPO}/actions/workflows", json={"workflows": [
        {"id": 1, "name": "Labeler", "path": ".github/workflows/labeler.yml", "state": "active"},
        {"id": 2, "name": "Build", "path": ".github/workflows/build.yml", "state": "active"},
        {"id": 3, "name": "Unit tests", "path": ".github/workflows/unit.yml", "state": "active"},
        {"id": 4, "name": "Old tests", "path": ".github/workflows/old.yml", "state": "disabled_manually"},
    ]})
    responses.get(f"{API}/repos/{REPO}/actions/workflows/3/runs", json={"workflow_runs": runs})
    responses.get(f"{API}/repos/{REPO}/actions/workflows/2/runs", json={"workflow_runs": []})
    for r in runs:
        responses.get(f"{API}/repos/{REPO}/actions/runs/{r['id']}", json=r)
        responses.get(f"{API}/repos/{REPO}/actions/runs/{r['id']}/artifacts",
                      json={"artifacts": run_artifacts.get(r["id"], [])})
    for art_id, blob in blobs.items():
        responses.get(f"{API}/repos/{REPO}/actions/artifacts/{art_id}/zip", body=blob)


@responses.activate
def test_discover_falls_back_to_test_workflows_on_5xx(client):
    mock_big_repo([run(1), run(2), run(3)], {1: [art(10, 1)], 3: [art(30, 3, name="junit-x")]}, {})
    res = discover(client, REPO)
    assert res.runs_with_junit == 2 and res.error is None
    assert "[workflows: Unit tests]" in res.summary()
    urls = [c.request.url for c in responses.calls]
    assert not any("/workflows/1/runs" in u or "/workflows/4/runs" in u for u in urls)


@responses.activate
def test_import_uses_fallback(engine, client, count):
    mock_big_repo([run(1), run(2)], {1: [art(10, 1)], 2: [art(20, 2)]},
                  {10: make_zip({"r.xml": XML_A}), 20: make_zip({"r.xml": XML_B})})
    out = import_repo(engine, client, REPO, HistoryConfig(max_runs=1))
    assert out.runs_imported == 1 and count("runs") == 1


@responses.activate
def test_client_errors_are_not_masked_by_fallback(client):
    responses.get(f"{API}/repos/{REPO}", json={"default_branch": "main"})
    responses.get(f"{API}/repos/{REPO}/actions/artifacts", status=403, json={"message": "Forbidden"})
    assert "403" in discover(client, REPO).summary()


def test_discover_cli_emits_one_annotation(tmp_path, monkeypatch, capsys):
    from flakydetect import history as hist

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    (tmp_path / "history.toml").write_text('[history]\ncandidates = ["a/%d" ]\n'.replace("%d", "b"))
    monkeypatch.setattr(cli.GitHubClient, "rate_limit", lambda self: "API quota 1/1")
    monkeypatch.setattr(hist, "discover", lambda c, repo, *a: hist.DiscoverResult(repo, branch="main"))
    assert cli.main(["discover-history", "x/1", "x/2", "x/3"]) == 0
    out = capsys.readouterr().out
    assert out.count("::notice") == 1 and "x/3: 0 runs" in out


@responses.activate
def test_result_cap_is_strict(engine, client, count):
    # XML_A has 2 results per run; a cap of 3 allows the first run only.
    mock_repo([run(1), run(2)], [art(20, 2), art(10, 1)],
              {20: make_zip({"r.xml": XML_A}), 10: make_zip({"r.xml": XML_A})})
    out = import_repo(engine, client, REPO, HistoryConfig(max_results_per_repo=3))
    assert out.results == 2 and out.runs_imported == 1
    assert "max_results_per_repo=3" in out.stopped
    assert count("test_results") == 2


@responses.activate
def test_import_stops_when_database_too_big(engine, client, count, monkeypatch):
    from flakydetect import db as dbmod
    sizes = iter([10.0, 400.0])
    monkeypatch.setattr(dbmod, "database_size_mb", lambda e: next(sizes))
    mock_repo([run(1), run(2)], [art(20, 2), art(10, 1)],
              {20: make_zip({"r.xml": XML_B}), 10: make_zip({"r.xml": XML_B})})
    out = import_repo(engine, client, REPO, HistoryConfig(max_db_mb=300))
    assert out.runs_imported == 1 and "400 MB, over the 300 MB limit" in out.stopped
    assert count("runs") == 1


def test_database_size_mb(engine):
    from flakydetect import db as dbmod
    assert dbmod.database_size_mb(engine) > 0


def test_import_cli_refuses_when_db_full(tmp_path, monkeypatch, capsys):
    from flakydetect import history as hist

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("FLAKY_DATABASE_URL", f"sqlite:///{tmp_path / 'f.db'}")
    (tmp_path / "history.toml").write_text('[history]\nrepos = ["a/b"]\nmax_db_mb = 0\n')
    called = []
    monkeypatch.setattr(hist, "import_repo", lambda *a, **k: called.append(1))
    assert cli.main(["import-history"]) == 3
    assert "not importing" in capsys.readouterr().err and not called


def test_db_size_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLAKY_DATABASE_URL", f"sqlite:///{tmp_path / 'f.db'}")
    assert cli.main(["db-size"]) == 0
    assert "database" in capsys.readouterr().out
