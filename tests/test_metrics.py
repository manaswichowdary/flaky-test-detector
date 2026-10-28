import json
from datetime import datetime, timedelta

from sqlalchemy import insert, select

from flakydetect import cli, db, metrics
from flakydetect.collector import RunMeta, ingest
from flakydetect.config import Settings
from flakydetect.junit import FAILED, PASSED, TestResult
from flakydetect.scorer import score_repo

T0 = datetime(2026, 10, 1, 12, 0)
DEMO = "o/demo"
PLANTED = {"test_a::t1": "timing", "test_a::t2": "network"}


def r(test, status, rf=0):
    module, name = test.split("::")
    return TestResult(test_id=f"demo.tests.{test}", classname=f"demo.tests.{module}", name=name,
                      status=status, rerun_failures=rf)


def shard(engine, key, sha, minutes, results, source="demo", repo=DEMO):
    ingest(engine, repo, RunMeta(run_id=key, sha=sha, source=source,
                                 completed_at=T0 + timedelta(minutes=minutes)), results)


def seed(engine):
    # Workflow run 100 on s1: t1 passes on one shard and fails on the other (mixed commit).
    shard(engine, "100-demo-1", "s1", 0, [r("test_a::t1", PASSED), r("test_a::t2", PASSED), r("test_a::t3", PASSED)])
    shard(engine, "100-demo-2", "s1", 1, [r("test_a::t1", FAILED), r("test_a::t2", PASSED), r("test_a::t3", PASSED)])
    # Workflow run 200 on s2: t1 fails again; already known flaky, so no rerun needed.
    shard(engine, "200-demo-1", "s2", 60, [r("test_a::t1", FAILED), r("test_a::t2", PASSED), r("test_a::t3", PASSED)])
    shard(engine, "200-demo-2", "s2", 61, [r("test_a::t1", PASSED), r("test_a::t2", PASSED), r("test_a::t3", PASSED),
                                           r("test_b::t4", PASSED, rf=1)])
    # Workflow run 300 on s3: a stable test fails for real.
    shard(engine, "300-demo-1", "s3", 120, [r("test_a::t1", PASSED), r("test_a::t3", FAILED)])
    # Imported history from another repo.
    shard(engine, "555/junit", "h1", 0, [r("test_x::y", PASSED), r("test_x::z", PASSED)],
          source="history", repo="up/stream")
    shard(engine, "556/junit", "h2", 5, [r("test_x::y", PASSED)], source="history", repo="up/stream")
    score_repo(engine, DEMO, Settings(), now=T0 + timedelta(hours=3))
    with engine.begin() as conn:
        pk = conn.execute(select(db.tests.c.id).where(db.tests.c.test_id == "demo.tests.test_a::t1")).scalar_one()
        conn.execute(insert(db.quarantine_issues).values(
            test_pk=pk, issue_number=2, issue_url="u", state="open",
            created_at=T0 + timedelta(minutes=1 + 30)))


def test_metrics_known_numbers(engine):
    seed(engine)
    m = metrics.compute(engine, DEMO, PLANTED)
    assert m.planted_found == ["test_a::t1"] and m.planted_missed == ["test_a::t2"]
    assert m.false_alarms == ["demo.tests.test_b::t4"]
    assert (m.demo_workflow_runs, m.failed_workflow_runs, m.reruns_avoided, m.real_failure_runs) == (3, 3, 2, 1)
    assert m.time_to_issue_minutes == [30.0] and m.median_minutes == 30.0
    assert (m.test_runs, m.workflow_runs) == (7, 5)
    assert m.repos == {DEMO: 3, "up/stream": 2}
    assert m.demo_test_runs == 5


def test_history_sampling_reported(engine):
    seed(engine)
    m = metrics.compute(engine, DEMO, PLANTED, sample={"up/stream": 40})
    [h] = m.history
    assert (h.repo, h.workflow_runs, h.test_runs, h.sample_one_in) == ("up/stream", 2, 2, 40)
    assert (h.sampled_tests, h.sampled_results) == (2, 3)
    assert h.approx_full_results == 120 and h.approx_full_results_per_run == 60
    md = m.markdown()
    assert "Imported history is **sampled**" in md
    assert "| up/stream | 2 | 1 in 40 tests | 2 | 3 | ≈ 80 tests, ≈ 60 results/run, ≈ 120 results |" in md
    assert "Live: 5 demo test runs + 2 imported from 2 public workflow runs" in md


def test_markdown_and_roundtrip(engine):
    seed(engine)
    m = metrics.compute(engine, DEMO, PLANTED, now=T0)
    md = m.markdown()
    assert "| Planted flaky tests found | **1 of 2** | Live: `demo.yml` runs on GitHub (3 workflow runs, 5 test runs) |" in md
    assert "**2 of 3** failed CI runs" in md
    assert "median 30.0 min, max 30.0 min (1 issues)" in md
    again = metrics.Metrics.from_dict(json.loads(m.to_json()))
    assert again.markdown() == md
    sampled = metrics.compute(engine, DEMO, PLANTED, now=T0, sample={"up/stream": 40})
    assert metrics.Metrics.from_dict(json.loads(sampled.to_json())).markdown() == sampled.markdown()


def test_workflow_run_id():
    assert metrics.workflow_run_id("40757057762-demo-2") == "40757057762"
    assert metrics.workflow_run_id("555/test-results-full") == "555"
    assert metrics.workflow_run_id("local") == "local"


def test_fmt_minutes():
    assert metrics.fmt_minutes(5.25) == "5.2 min" and metrics.fmt_minutes(180) == "3.0 h"
    assert metrics.fmt_minutes(None) == "n/a"


def test_cli_metrics(engine, tmp_path, monkeypatch, capsys):
    seed(engine)
    monkeypatch.setenv("FLAKY_DATABASE_URL", engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(cli, "_load_planted", lambda: PLANTED)
    cfg = tmp_path / "history.toml"
    cfg.write_text('[history.repo_sample]\n"up/stream" = 40\n')
    out = tmp_path / "m.json"
    assert cli.main(["metrics", "--repo", DEMO, "--json", str(out), "--history-config", str(cfg)]) == 0
    data = json.loads(out.read_text())
    assert data["reruns_avoided"] == 2 and data["history"][0]["sample_one_in"] == 40
    assert "Planted flaky tests found" in capsys.readouterr().out
