import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert, select

from flakydetect import cli, db, report
from flakydetect.collector import RunMeta, ingest
from flakydetect.config import Settings
from flakydetect.junit import FAILED, PASSED, TestResult
from flakydetect.scorer import score_repo

NOW = datetime(2026, 10, 28, 12, 0)


def res(name, status, rf=0):
    return TestResult(test_id=f"m::{name}", classname="m", name=name, status=status, rerun_failures=rf)


def seed(engine):
    """test_flaky: mixed on one commit; test_rerun: pass on rerun; test_broken: always fails;
    test_ok: always passes; test_<script>: flaky with HTML in its name."""
    for i, (flaky, script) in enumerate([(PASSED, FAILED), (FAILED, PASSED), (PASSED, PASSED)]):
        ingest(engine, "o/a", RunMeta(run_id=str(i), sha="c1", completed_at=NOW - timedelta(hours=3 - i)), [
            res("test_flaky", flaky), res("test_ok", PASSED), res("test_broken", FAILED),
            res("test_rerun", PASSED, rf=1 if i == 2 else 0), res("test_<script>", script),
        ])
    ingest(engine, "o/b", RunMeta(run_id="1", sha="x", completed_at=NOW), [res("test_b", PASSED)])
    for repo in ("o/a", "o/b"):
        score_repo(engine, repo, Settings(), now=NOW)


def test_collect_orders_and_filters(engine):
    seed(engine)
    data = report.collect(engine, now=NOW)
    ids = [r.test_id for r in data.rows]
    assert "m::test_ok" not in ids and "m::test_b" not in ids  # no evidence, no failures
    assert ids[-1] == "m::test_broken"                        # score 0, failures > 0 last
    assert {r.test_id for r in data.rows if r.is_flaky} == {"m::test_flaky", "m::test_rerun", "m::test_<script>"}
    flaky = next(r for r in data.rows if r.test_id == "m::test_flaky")
    assert flaky.recent == ["p", "f", "p"]                    # oldest -> newest
    rerun = next(r for r in data.rows if r.test_id == "m::test_rerun")
    assert rerun.recent[-1] == "r"
    assert [(r.repo, r.runs, r.flaky) for r in data.repos] == [("o/a", 3, 3), ("o/b", 1, 0)]
    assert data.total_runs == 4


def test_only_open_issues_are_linked(engine):
    seed(engine)
    with engine.begin() as conn:
        pks = dict(conn.execute(select(db.tests.c.test_id, db.tests.c.id)).all())
        conn.execute(insert(db.quarantine_issues), [
            {"test_pk": pks["m::test_flaky"], "issue_number": 7, "issue_url": "https://github.com/o/a/issues/7",
             "state": "open", "created_at": NOW},
            {"test_pk": pks["m::test_rerun"], "issue_number": 8, "issue_url": "https://github.com/o/a/issues/8",
             "state": "closed", "created_at": NOW},
        ])
    rows = {r.test_id: r for r in report.collect(engine, now=NOW).rows}
    assert rows["m::test_flaky"].issue_number == 7
    assert rows["m::test_rerun"].issue_url is None


def test_render_escapes_test_names(engine):
    seed(engine)
    html = report.render(report.collect(engine, now=NOW))
    assert "m::test_&lt;script&gt;" in html and "<script>" in html  # only our own script tag
    assert html.count("<script>") == 1


def test_cli_report_rescore(engine, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    url = engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("FLAKY_DATABASE_URL", url)
    seed(engine)
    assert cli.main(["report", "--rescore", "--out", str(tmp_path / "out")]) == 0
    assert "flaky tests" in capsys.readouterr().out
    assert (tmp_path / "out" / "index.html").exists()


# --- Playwright: the generated page in a real browser ------------------------------------

@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or None)
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium for Playwright: {str(exc).splitlines()[0]}")
        yield b
        b.close()


@pytest.fixture
def page_for(browser, tmp_path):
    def _open(data):
        path = tmp_path / "index.html"
        path.write_text(report.render(data), encoding="utf-8")
        page = browser.new_page()
        page.goto(path.as_uri())
        return page
    return _open


def visible_ids(page):
    return page.eval_on_selector_all("#tests tbody tr:not([hidden])", "rows => rows.map(r => r.dataset.test)")


def test_page_sorts_columns(engine, page_for):
    seed(engine)
    page = page_for(report.collect(engine, now=NOW))
    assert visible_ids(page)[-1] == "m::test_broken"
    page.click("th[data-key=failures] button")  # descending first
    assert visible_ids(page)[0] == "m::test_broken"
    assert page.get_attribute("th[data-key=failures]", "aria-sort") == "descending"
    page.click("th[data-key=failures] button")
    assert page.get_attribute("th[data-key=failures]", "aria-sort") == "ascending"
    page.click("th[data-key=test] button")
    page.click("th[data-key=test] button")  # ascending by name
    ids = visible_ids(page)
    assert ids == sorted(ids)


def test_page_filters(engine, page_for):
    seed(engine)
    page = page_for(report.collect(engine, now=NOW))
    page.fill("#search", "rerun")
    assert visible_ids(page) == ["m::test_rerun"]
    page.fill("#search", "zzz")
    assert visible_ids(page) == [] and page.is_visible("#no-match")
    page.fill("#search", "")
    page.check("#flaky-only")
    assert "m::test_broken" not in visible_ids(page)
    page.select_option("#repo-filter", "o/b")
    assert visible_ids(page) == []


def test_page_empty_state(engine, page_for):
    page = page_for(report.collect(engine, now=NOW))
    assert page.is_visible("#empty-state")
    assert page.inner_text("#empty-state") == "No flaky tests or failures recorded yet."


def test_page_issue_links(engine, page_for):
    seed(engine)
    data = report.collect(engine, now=NOW)
    data.rows[0].issue_number, data.rows[0].issue_url = 42, "https://github.com/o/a/issues/42"
    page = page_for(data)
    link = page.locator("#tests a", has_text="#42")
    assert link.get_attribute("href") == "https://github.com/o/a/issues/42"
