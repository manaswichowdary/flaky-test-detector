from datetime import datetime, timedelta

import pytest

from flakydetect.collector import RunMeta, ingest
from flakydetect.config import Settings, load
from flakydetect.junit import ERROR, FAILED, PASSED, SKIPPED, TestResult
from flakydetect.scorer import _Row, classify_run_failures, compute, score_repo

NOW = datetime(2026, 6, 1)
S = Settings()


def rows(*spec, days_ago=0):
    """spec items: (sha, status) or (sha, status, rerun_failures) or (sha, status, rf, run, attempt)."""
    out = []
    for i, item in enumerate(spec):
        sha, status, rf, run, attempt = (*item, 0, None, 1)[:5] if len(item) < 5 else item
        out.append(_Row(run or f"r{i}", attempt, sha, NOW - timedelta(days=days_ago, minutes=len(spec) - i),
                        status, rf))
    return out


@pytest.mark.parametrize("name,spec,flaky,mixed,reruns", [
    ("always passes", [("a", PASSED)] * 5, False, 0, 0),
    ("always fails = broken", [("a", FAILED), ("b", FAILED), ("c", ERROR)], False, 0, 0),
    ("mixed on one commit", [("a", PASSED), ("a", FAILED), ("b", PASSED)], True, 1, 0),
    ("fail then pass in-run rerun", [("a", PASSED, 1), ("b", PASSED)], True, 0, 1),
    ("real regression then fix", [("a", PASSED), ("b", FAILED), ("c", PASSED)], False, 0, 0),
    ("skips are ignored", [("a", PASSED), ("a", SKIPPED)], False, 0, 0),
])
def test_table(name, spec, flaky, mixed, reruns):
    out = compute(rows(*spec), S, NOW)
    assert (out["is_flaky"], out["mixed_commits"], out["rerun_passes"]) == (flaky, mixed, reruns), name


def test_rerun_attempt_counts():
    out = compute(rows(("a", FAILED, 0, "r1", 1), ("a", PASSED, 0, "r1", 2)), S, NOW)
    assert out["rerun_passes"] == 1 and out["mixed_commits"] == 1 and out["is_flaky"]


def test_fix_followed_by_clean_commits_clears():
    spec = [("a", PASSED), ("a", FAILED)] + [(f"c{i}", PASSED) for i in range(10)]
    no_floor = Settings(min_score=0.0)
    assert not compute(rows(*spec), no_floor, NOW)["is_flaky"]
    assert compute(rows(*spec[:-1]), no_floor, NOW)["is_flaky"]  # 9 clean commits: not cleared yet


def test_score_floor():
    spec = [("a", PASSED), ("a", FAILED)] + [(f"c{i}", PASSED) for i in range(9)]
    out = compute(rows(*spec), Settings(min_score=0.2, clear_after_clean_commits=100), NOW)
    assert out["score"] == pytest.approx(0.1, abs=0.01) and not out["is_flaky"]


def test_decay_weights_recent_commits_more():
    old = rows(("a", PASSED), ("a", FAILED), days_ago=28)
    recent = rows(("b", PASSED), days_ago=0)
    out = compute(old + recent, S, NOW)
    # 2 half-lives: weight 0.25 vs 1.0 -> 0.25 / 1.25
    assert out["score"] == pytest.approx(0.2, abs=0.01)


def test_thresholds_from_env(tmp_path):
    spec = rows(("a", PASSED), ("a", FAILED))
    strict = load(tmp_path / "none.toml", env={"FLAKY_MIN_MIXED_COMMITS": "2", "FLAKY_MIN_RERUN_PASSES": "5"})
    assert compute(spec, S, NOW)["is_flaky"]
    assert not compute(spec, strict, NOW)["is_flaky"]


def test_empty_history():
    out = compute([], S, NOW)
    assert out["score"] == 0 and not out["is_flaky"] and out["last_failure_at"] is None


def _res(name, status, rf=0):
    return TestResult(test_id=f"m::{name}", classname="m", name=name, status=status, rerun_failures=rf)


def test_score_repo_persists_and_respects_window(engine, count):
    t = NOW - timedelta(days=1)
    ingest(engine, "o/r", RunMeta(run_id="1", sha="a", completed_at=t), [_res("flaky", PASSED), _res("ok", PASSED)])
    ingest(engine, "o/r", RunMeta(run_id="2", sha="a", completed_at=t), [_res("flaky", FAILED), _res("ok", PASSED)])
    ingest(engine, "o/r", RunMeta(run_id="0", sha="z", completed_at=NOW - timedelta(days=90)),
           [_res("ok", PASSED), _res("ok2", FAILED)])
    scores = {s.test_id: s for s in score_repo(engine, "o/r", S, now=NOW)}
    assert scores["m::flaky"].is_flaky and scores["m::flaky"].score == 1.0
    assert not scores["m::ok"].is_flaky and scores["m::ok"].runs_seen == 2
    assert scores["m::ok2"].runs_seen == 0
    assert count("test_scores") == 3
    score_repo(engine, "o/r", S, now=NOW)  # re-score replaces rows
    assert count("test_scores") == 3


def test_classify_run_failures(engine):
    t = NOW - timedelta(hours=1)
    ingest(engine, "o/r", RunMeta(run_id="1", sha="a", completed_at=t),
           [_res("flaky", PASSED), _res("sha_mixed", PASSED), _res("real", PASSED)])
    ingest(engine, "o/r", RunMeta(run_id="2", sha="a", completed_at=t),
           [_res("flaky", FAILED), _res("sha_mixed", PASSED), _res("real", PASSED)])
    score_repo(engine, "o/r", S, now=NOW)
    out = ingest(engine, "o/r", RunMeta(run_id="3", sha="b", completed_at=NOW),
                 [_res("flaky", FAILED), _res("sha_mixed", PASSED), _res("real", FAILED)])
    ingest(engine, "o/r", RunMeta(run_id="4", sha="b", completed_at=NOW),
           [_res("flaky", PASSED), _res("sha_mixed", FAILED), _res("real", FAILED)])
    verdicts = {v.test_id: v for v in classify_run_failures(engine, out.run_pk)}
    assert set(verdicts) == {"m::flaky", "m::real"}
    assert verdicts["m::flaky"].flaky and "known flaky" in verdicts["m::flaky"].reason
    assert not verdicts["m::real"].flaky
