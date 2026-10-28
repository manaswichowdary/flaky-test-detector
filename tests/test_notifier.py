from datetime import datetime, timedelta

import pytest
from fakegithub import FakeGitHub
from sqlalchemy import select

from flakydetect import db
from flakydetect.collector import RunMeta, ingest
from flakydetect.config import Settings
from flakydetect.junit import FAILED, PASSED, TestResult
from flakydetect.notifier import COMMENT_MARKER, notify, quarantined_tests, write_quarantine_file
from flakydetect.scorer import score_repo

NOW = datetime(2026, 6, 1)


def res(name, status, message=None):
    return TestResult(test_id=f"m::{name}", classname="m", name=name, status=status, message=message)


def seed(engine, settings, sha="s1"):
    """test_flaky fails once on a commit where it also passes; test_real fails twice."""
    t = NOW - timedelta(minutes=10)
    ingest(engine, "o/r", RunMeta(run_id="1", sha=sha, completed_at=t),
           [res("test_flaky", PASSED), res("test_real", FAILED, "AssertionError: 1 != 2")])
    ingest(engine, "o/r", RunMeta(run_id="2", sha=sha, completed_at=t),
           [res("test_flaky", FAILED, "timeout"), res("test_real", FAILED, "AssertionError: 1 != 2")])
    score_repo(engine, "o/r", settings, now=NOW)


@pytest.fixture
def settings(tmp_path):
    return Settings(quarantine_file=str(tmp_path / "quarantine.txt"))


def test_issue_mode_comments_and_opens_issue(engine, settings):
    gh = FakeGitHub()
    seed(engine, settings)
    out = notify(engine, gh, settings, "o/r", "s1", pr_number=7)
    assert out.issues_opened == ["m::test_flaky"]
    [comment] = gh.comments[7]
    body = comment["body"]
    assert COMMENT_MARKER in body
    assert "1 failure(s) look flaky" in body and "`m::test_flaky`" in body
    assert "1 failure(s) look real" in body and "AssertionError" in body
    assert f"#{gh.issues[0]['number']}" in body
    assert gh.issues[0]["labels"] == ["flaky-test"]
    with engine.connect() as conn:
        row = conn.execute(select(db.quarantine_issues)).one()
    assert row.created_at == datetime(2026, 6, 1, 0, 5) and row.failure_at is not None


def test_second_notify_updates_comment_and_no_duplicate_issue(engine, settings):
    gh = FakeGitHub()
    seed(engine, settings)
    notify(engine, gh, settings, "o/r", "s1", pr_number=7)
    ingest(engine, "o/r", RunMeta(run_id="3", sha="s1", completed_at=NOW),
           [res("test_flaky", PASSED), res("test_real", PASSED)])
    out = notify(engine, gh, settings, "o/r", "s1", pr_number=7)
    assert out.issues_opened == [] and len(gh.issues) == 1
    assert len(gh.comments[7]) == 1
    assert gh.calls.count("create_comment") == 1


def test_existing_github_issue_is_reused(engine, settings):
    gh = FakeGitHub()
    seed(engine, settings)
    gh.create_issue("o/r", "x", "<!-- flakydetect:test m::test_flaky -->", ["flaky-test"])
    gh.calls.clear()
    out = notify(engine, gh, settings, "o/r", "s1", pr_number=None)
    assert out.issues_opened == [] and "create_issue" not in gh.calls
    assert quarantined_tests(engine, "o/r") == ["m::test_flaky"]


def test_closed_issue_is_forgotten_and_reopened_if_still_flaky(engine, settings):
    gh = FakeGitHub()
    seed(engine, settings)
    notify(engine, gh, settings, "o/r", "s1", pr_number=None)
    gh.issues[0]["state"] = "closed"
    out = notify(engine, gh, settings, "o/r", "s1", pr_number=None)
    assert out.issues_opened == ["m::test_flaky"] and len(gh.issues) == 2


def test_warn_mode_never_opens_issues(engine, tmp_path):
    settings = Settings(mode="warn", quarantine_file=str(tmp_path / "q.txt"))
    gh = FakeGitHub()
    seed(engine, settings)
    out = notify(engine, gh, settings, "o/r", "s1", pr_number=7)
    assert gh.issues == [] and out.issues_opened == []
    assert "look flaky" in gh.comments[7][0]["body"]
    assert not (tmp_path / "q.txt").exists()


def test_auto_mode_writes_quarantine_file(engine, tmp_path):
    q = tmp_path / "quarantine.txt"
    settings = Settings(mode="auto", quarantine_file=str(q))
    gh = FakeGitHub()
    seed(engine, settings)
    out = notify(engine, gh, settings, "o/r", "s1", pr_number=None)
    assert out.quarantined == ["m::test_flaky"]
    assert q.read_text() == "m::test_flaky\n"


def test_no_failures_no_comment(engine, settings):
    gh = FakeGitHub()
    ingest(engine, "o/r", RunMeta(run_id="1", sha="ok", completed_at=NOW), [res("t", PASSED)])
    score_repo(engine, "o/r", settings, now=NOW)
    out = notify(engine, gh, settings, "o/r", "ok", pr_number=3)
    assert out.comment_id is None and gh.comments == {}


def test_write_quarantine_file_merges(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("b\n")
    write_quarantine_file(q, {"a", "b"})
    assert q.read_text() == "a\nb\n"


def test_plugin_marks_quarantined_tests_xfail(pytester):
    pytester.makepyfile(test_q="def test_bad():\n    assert 0\n\ndef test_other():\n    assert 0\n")
    (pytester.path / "q.txt").write_text("test_q::test_bad\n")
    result = pytester.runpytest("-p", "no:asyncio", "-p", "flakydetect.pytest_plugin",
                                "--flaky-quarantine=q.txt")
    result.assert_outcomes(failed=1, xfailed=1)


def test_junit_test_id():
    from flakydetect.pytest_plugin import junit_test_id
    assert junit_test_id("demo/tests/test_cart.py::Cls::test_x[1]") == "demo.tests.test_cart.Cls::test_x[1]"
