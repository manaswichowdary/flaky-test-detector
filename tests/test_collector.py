from datetime import datetime, timezone

from sqlalchemy import select

from flakydetect import db
from flakydetect.collector import RunMeta, ingest
from flakydetect.junit import FAILED, PASSED, TestResult


def results(*pairs):
    return [TestResult(test_id=f"m::{n}", classname="m", name=n, status=s) for n, s in pairs]


def test_one_row_per_test_per_run(engine, count):
    ingest(engine, "o/r", RunMeta(run_id="1", sha="a"), results(("t1", PASSED), ("t2", FAILED)))
    ingest(engine, "o/r", RunMeta(run_id="2", sha="a"), results(("t1", PASSED), ("t2", PASSED)))
    assert count("runs") == 2
    assert count("tests") == 2
    assert count("test_results") == 4


def test_reingest_same_run_is_idempotent(engine, count):
    meta = RunMeta(run_id="9", sha="a")
    first = ingest(engine, "o/r", meta, results(("t1", PASSED)))
    second = ingest(engine, "o/r", meta, results(("t1", PASSED)))
    assert not first.duplicate and second.duplicate and second.run_pk == first.run_pk
    assert count("test_results") == 1


def test_rerun_attempt_is_a_separate_run(engine, count):
    ingest(engine, "o/r", RunMeta(run_id="9", sha="a", run_attempt=1), results(("t1", FAILED)))
    ingest(engine, "o/r", RunMeta(run_id="9", sha="a", run_attempt=2), results(("t1", PASSED)))
    assert count("runs") == 2 and count("test_results") == 2


def test_same_test_twice_in_one_run_is_merged(engine, count):
    out = ingest(engine, "o/r", RunMeta(run_id="1", sha="a"), results(("t1", FAILED), ("t1", PASSED)))
    assert out.inserted == 1
    with engine.connect() as conn:
        row = conn.execute(select(db.test_results)).one()
    assert row.status == PASSED and row.rerun_failures == 1


def test_run_metadata_stored_as_naive_utc(engine):
    done = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    meta = RunMeta(run_id="1", sha="abc", branch="feat", pr_number=7, workflow="CI",
                   source="demo", completed_at=done)
    ingest(engine, "o/r", meta, results(("t1", PASSED)))
    with engine.connect() as conn:
        run = conn.execute(select(db.runs)).one()
    assert (run.sha, run.branch, run.pr_number, run.source) == ("abc", "feat", 7, "demo")
    assert run.completed_at == datetime(2026, 1, 2, 3, 4, 5)


def test_repos_are_isolated(engine, count):
    ingest(engine, "o/a", RunMeta(run_id="1", sha="a"), results(("t1", PASSED)))
    ingest(engine, "o/b", RunMeta(run_id="1", sha="a"), results(("t1", PASSED)))
    assert count("tests") == 2 and count("runs") == 2


class _Orig(Exception):
    def __init__(self, sqlstate):
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


def test_ingest_retries_deadlock_then_succeeds(engine, monkeypatch, count):
    from sqlalchemy.exc import OperationalError

    from flakydetect import collector

    real, calls = collector._ingest_once, []

    def flaky_once(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise OperationalError("INSERT ...", {}, _Orig("40P01"))
        return real(*args, **kwargs)

    monkeypatch.setattr(collector, "_ingest_once", flaky_once)
    sleeps = []
    out = ingest(engine, "o/r", RunMeta(run_id="1", sha="a"), results(("t1", PASSED)), sleep=sleeps.append)
    assert out.inserted == 1 and len(calls) == 3 and len(sleeps) == 2
    assert count("test_results") == 1


def test_ingest_does_not_retry_other_errors(engine, monkeypatch):
    import pytest
    from sqlalchemy.exc import ProgrammingError

    from flakydetect import collector

    def broken(*args, **kwargs):
        raise ProgrammingError("SELECT", {}, _Orig("42P01"))

    monkeypatch.setattr(collector, "_ingest_once", broken)
    with pytest.raises(ProgrammingError):
        ingest(engine, "o/r", RunMeta(run_id="1", sha="a"), results(("t1", PASSED)), sleep=lambda s: None)


def test_concurrent_shards_do_not_deadlock(engine, count):
    """Six shards of the same commit writing the same tests at once (as in demo.yml)."""
    if engine.dialect.name != "postgresql":
        import pytest
        pytest.skip("needs a real concurrent database")
    from concurrent.futures import ThreadPoolExecutor

    names = [(f"t{i:02d}", PASSED if i % 3 else FAILED) for i in range(30)]

    def shard(i):
        # Different input order per shard, like differently shuffled test runs.
        order = names[i:] + names[:i]
        return ingest(engine, "o/r", RunMeta(run_id=f"run-{i}", sha="same"), results(*order))

    with ThreadPoolExecutor(6) as pool:
        outs = list(pool.map(shard, range(6)))
    assert all(o.inserted == 30 for o in outs)
    assert count("tests") == 30 and count("test_results") == 180
