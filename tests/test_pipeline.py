"""End-to-end: CLI ingest -> score -> notify on fixture XML, GitHub mocked."""

import json

import pytest
from fakegithub import FakeGitHub

from flakydetect import cli

RUN = """<testsuite>
  <testcase classname="demo.tests.test_x" name="test_flaky">{flaky}</testcase>
  <testcase classname="demo.tests.test_x" name="test_ok"/>
</testsuite>"""
FAIL = '<failure message="timeout"/>'


@pytest.fixture
def gh(monkeypatch, tmp_path):
    fake = FakeGitHub()
    monkeypatch.setattr(cli, "GitHubClient", lambda: fake)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLAKY_DATABASE_URL", f"sqlite:///{tmp_path / 'f.db'}")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 4, "head": {"sha": "headsha"}}}))
    for key, value in {
        "GITHUB_REPOSITORY": "o/demo", "GITHUB_SHA": "mergesha", "GITHUB_RUN_ID": "100",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": "refs/pull/4/merge", "GITHUB_HEAD_REF": "feat",
        "GITHUB_EVENT_PATH": str(event), "GITHUB_WORKFLOW": "demo",
    }.items():
        monkeypatch.setenv(key, value)
    return fake


def test_matrix_runs_then_notify(gh, tmp_path, capsys):
    for shard, flaky in [(1, ""), (2, FAIL), (3, "")]:
        xml = tmp_path / f"shard{shard}.xml"
        xml.write_text(RUN.format(flaky=flaky))
        assert cli.main(["ingest", str(xml), "--run-id", f"100-demo-{shard}", "--source", "demo"]) == 0
    assert cli.main(["score"]) == 0
    assert "1 flaky" in capsys.readouterr().out
    assert cli.main(["notify"]) == 0
    assert "issues opened: 1" in capsys.readouterr().out
    [comment] = gh.comments[4]
    assert "headsha"[:7] in comment["body"]
    assert "demo.tests.test_x::test_flaky" in gh.issues[0]["title"]
    # Re-running notify (e.g. a re-run job) keeps one comment and one issue.
    assert cli.main(["notify"]) == 0
    assert len(gh.comments[4]) == 1 and len(gh.issues) == 1


def test_notify_without_token_fails_clearly(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("FLAKY_DATABASE_URL", f"sqlite:///{tmp_path / 'f.db'}")
    assert cli.main(["notify", "--repo", "o/r", "--sha", "a"]) == 2
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().err


def test_quarantine_list_only_in_auto_mode(gh, monkeypatch, capsys):
    assert cli.main(["quarantine-list"]) == 0
    assert capsys.readouterr().out == ""
