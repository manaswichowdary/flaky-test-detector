import json
from pathlib import Path

from flakydetect import cli, ghenv

FIX = Path(__file__).parent / "fixtures" / "junit"


def test_run_metadata_push():
    meta = ghenv.run_metadata({
        "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "55", "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": "abc", "GITHUB_REF_NAME": "main", "GITHUB_REF": "refs/heads/main",
    })
    assert meta["repo"] == "o/r" and meta["run_attempt"] == 2
    assert meta["branch"] == "main" and meta["pr_number"] is None


def test_run_metadata_pull_request_uses_head_sha(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 12, "head": {"sha": "head123"}}}))
    meta = ghenv.run_metadata({
        "GITHUB_SHA": "merge999", "GITHUB_REF": "refs/pull/12/merge",
        "GITHUB_HEAD_REF": "feat", "GITHUB_EVENT_PATH": str(event),
    })
    assert meta["sha"] == "head123" and meta["pr_number"] == 12 and meta["branch"] == "feat"


def test_cli_ingest_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_EVENT_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FLAKY_DATABASE_URL", f"sqlite:///{tmp_path / 'f.db'}")
    args = ["ingest", str(FIX / "pytest.xml"), "--repo", "o/r", "--run-id", "1", "--sha", "abc"]
    assert cli.main(args) == 0
    assert "stored 4 results" in capsys.readouterr().out
    assert cli.main(args) == 0
    assert "already ingested" in capsys.readouterr().out


def test_cli_ingest_requires_metadata(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_EVENT_PATH"):
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["ingest", str(FIX / "pytest.xml")]) == 2
    assert "required" in capsys.readouterr().err


def test_cli_require_persistent_db_fails_without_url(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLAKY_DATABASE_URL", raising=False)
    rc = cli.main(["--require-persistent-db", "ingest", str(FIX / "pytest.xml"),
                   "--repo", "o/r", "--run-id", "1", "--sha", "a"])
    assert rc == 2 and "FLAKY_DATABASE_URL is not set" in capsys.readouterr().err
    assert not (tmp_path / "flakydetect.db").exists()


def test_cli_bad_xml_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLAKY_DATABASE_URL", f"sqlite:///{tmp_path / 'f.db'}")
    rc = cli.main(["ingest", str(FIX / "malformed.xml"), "--repo", "o/r", "--run-id", "1", "--sha", "a"])
    assert rc == 2 and "invalid JUnit XML" in capsys.readouterr().err
