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
    responses.get(f"{API}/repos/{REPO}", json={"default_branch": "main"})
    responses.get(f"{API}/repos/{REPO}/actions/runs", json={"workflow_runs": runs})
    for run_id, arts in artifacts.items():
        responses.get(f"{API}/repos/{REPO}/actions/runs/{run_id}/artifacts", json={"artifacts": arts})
    for art_id, blob in blobs.items():
        responses.get(f"{API}/repos/{REPO}/actions/artifacts/{art_id}/zip", body=blob)


def run(i, sha="s"):
    return {"id": i, "run_attempt": 1, "head_sha": f"{sha}{i}", "head_branch": "main",
            "name": "CI", "updated_at": "2026-10-20T10:00:00Z", "run_started_at": "2026-10-20T09:50:00Z"}


def art(i, name="test-results-py3.12", size=1000, expired=False):
    return {"id": i, "name": name, "size_in_bytes": size, "expired": expired}


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
])
def test_env_label(name, label):
    assert env_label(name) == label


@responses.activate
def test_import_filters_expired_and_names_and_is_resumable(engine, client, count):
    runs = [run(1), run(2), run(3)]
    mock_repo(runs, {
        1: [art(10), art(11, name="coverage-report")],
        2: [art(20, expired=True)],
        3: [art(30, name="junit-ubuntu"), art(31, name="junit-macos")],
    }, {10: make_zip({"r.xml": XML_A}), 30: make_zip({"r.xml": XML_A}), 31: make_zip({"r.xml": XML_B})})
    cfg = HistoryConfig(max_runs=10)
    out = import_repo(engine, client, REPO, cfg)
    assert (out.runs_imported, out.runs_without_junit, out.stored_runs, out.results) == (2, 1, 3, 5)
    assert count("runs") == 3 and count("test_results") == 5
    # macOS and ubuntu legs are separate tests.
    assert count("tests") == 5
    downloads = sum("/zip" in c.request.url for c in responses.calls)
    again = import_repo(engine, client, REPO, cfg)
    assert again.runs_already == 2 and again.results == 0
    assert sum("/zip" in c.request.url for c in responses.calls) == downloads  # no re-download
    assert count("test_results") == 5


@responses.activate
def test_import_respects_size_and_max_runs(engine, client):
    mock_repo([run(1), run(2)], {1: [art(10, size=500 * 1024 * 1024)], 2: [art(20)]},
              {20: make_zip({"r.xml": XML_B})})
    out = import_repo(engine, client, REPO, HistoryConfig(max_runs=10, max_artifact_mb=100))
    assert out.artifacts_skipped == 1 and out.runs_imported == 1
    assert import_repo(engine, client, "up/stream", HistoryConfig(max_runs=1)).runs_imported == 0


@responses.activate
def test_import_records_bad_zip(engine, client):
    mock_repo([run(1)], {1: [art(10)]}, {10: b"not a zip"})
    out = import_repo(engine, client, REPO, HistoryConfig())
    assert out.artifacts_skipped == 1 and "test-results" in out.errors[0]


@responses.activate
def test_discover_summary(client):
    mock_repo([run(1), run(2)], {1: [art(10, size=2 * 1024 * 1024)], 2: []}, {})
    res = discover(client, REPO)
    assert (res.runs_checked, res.runs_with_junit, res.avg_mb) == (2, 1, 2.0)
    assert "1/2 recent runs" in res.summary()


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


def test_import_history_cli_noop_without_repos(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "history.toml").write_text("[history]\nrepos = []\n")
    assert cli.main(["import-history"]) == 0
    assert "nothing to import" in capsys.readouterr().out
