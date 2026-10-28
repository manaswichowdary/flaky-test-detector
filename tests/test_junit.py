from pathlib import Path

import pytest

from flakydetect import junit
from flakydetect.junit import ERROR, FAILED, PASSED, SKIPPED, JUnitParseError

FIX = Path(__file__).parent / "fixtures" / "junit"


def by_name(results):
    return {r.name: r for r in results}


def test_pytest_fields_and_statuses():
    r = by_name(junit.parse_file(FIX / "pytest.xml"))
    ok = r["test_cart_sums_quantities"]
    assert ok.test_id == "demo.tests.test_cart::test_cart_sums_quantities"
    assert ok.status == PASSED and ok.duration == 0.001
    assert ok.file == "demo/tests/test_cart.py"
    assert ok.properties == {"fingerprint": "abc123"}
    assert r["test_book_full_price"].status == FAILED
    assert r["test_book_full_price"].message == "assert 15.0 == 20.0"
    assert r["test_slow"].status == ERROR
    assert r["test_skip"].status == SKIPPED


def test_surefire_reruns():
    r = by_name(junit.parse_file(FIX / "surefire.xml"))
    assert r["evictsOldest"].status == PASSED
    assert r["evictsOldest"].passed_on_rerun
    assert r["loadsLazily"].status == FAILED
    assert r["loadsLazily"].rerun_failures == 2
    assert not r["loadsLazily"].passed_on_rerun
    assert not r["stable"].passed_on_rerun


def test_nested_suites_and_bad_time():
    r = by_name(junit.parse_file(FIX / "nested.xml"))
    assert set(r) == {"t1", "t2"}
    assert r["t1"].duration == 1.5 and r["t2"].duration == 0.0


def test_repeated_testcase_merges_into_rerun_pass():
    xml = """<testsuite>
      <testcase classname="m" name="t"><failure message="boom"/></testcase>
      <testcase classname="m" name="t"/>
    </testsuite>"""
    [r] = junit.parse_string(xml)
    assert r.status == PASSED and r.rerun_failures == 1 and r.passed_on_rerun


def test_malformed_xml_gives_clear_error():
    with pytest.raises(JUnitParseError, match="malformed.xml.*invalid JUnit XML"):
        junit.parse_file(FIX / "malformed.xml")


def test_wrong_root_rejected():
    with pytest.raises(JUnitParseError, match="unexpected root"):
        junit.parse_string("<html/>")


def test_entity_expansion_blocked():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite>&a;</testsuite>'
    with pytest.raises(JUnitParseError):
        junit.parse_string(bomb)


def test_parse_paths_walks_directories(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.xml").write_text('<testsuite><testcase classname="c" name="n"/></testsuite>')
    (tmp_path / "notes.txt").write_text("ignore me")
    assert [r.test_id for r in junit.parse_paths([tmp_path])] == ["c::n"]
