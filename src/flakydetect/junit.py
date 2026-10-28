"""Parse JUnit XML (pytest, Maven Surefire, Gradle) into flat per-test results."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

PASSED, FAILED, ERROR, SKIPPED = "passed", "failed", "error", "skipped"

# Surefire: flaky* = failed then passed on rerun; rerun* = still failing on rerun.
_FLAKY_TAGS = {"flakyFailure", "flakyError"}
_RERUN_FAIL_TAGS = {"rerunFailure", "rerunError"}


class JUnitParseError(ValueError):
    pass


@dataclass
class TestResult:
    __test__ = False  # not a pytest test class

    test_id: str
    classname: str
    name: str
    status: str
    duration: float = 0.0
    file: str | None = None
    message: str | None = None
    rerun_failures: int = 0  # failed attempts before the final outcome
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def passed_on_rerun(self) -> bool:
        return self.status == PASSED and self.rerun_failures > 0


def make_test_id(classname: str, name: str) -> str:
    return f"{classname}::{name}" if classname else name


def _status_and_message(case: Element) -> tuple[str, str | None]:
    for tag, status in (("failure", FAILED), ("error", ERROR), ("skipped", SKIPPED)):
        child = case.find(tag)
        if child is not None:
            return status, child.get("message") or (child.text or "").strip()[:500] or None
    return PASSED, None


def _parse_case(case: Element) -> TestResult:
    classname = case.get("classname", "") or ""
    name = case.get("name")
    if not name:
        raise JUnitParseError("testcase without a name attribute")
    status, message = _status_and_message(case)
    reruns = sum(1 for c in case if c.tag in _FLAKY_TAGS | _RERUN_FAIL_TAGS)
    props = {
        p.get("name"): p.get("value", "")
        for p in case.iterfind("properties/property")
        if p.get("name")
    }
    try:
        duration = float(case.get("time") or 0)
    except ValueError:
        duration = 0.0
    return TestResult(
        test_id=make_test_id(classname, name),
        classname=classname,
        name=name,
        status=status,
        duration=duration,
        file=case.get("file"),
        message=message,
        rerun_failures=reruns,
        properties=props,
    )


def merge_attempts(results: list[TestResult]) -> list[TestResult]:
    """Repeated testcases with the same id (e.g. pytest-rerunfailures) become one result:
    the last attempt is the outcome, earlier failed attempts count as rerun failures."""
    merged: dict[str, TestResult] = {}
    for r in results:
        prev = merged.get(r.test_id)
        if prev is None:
            merged[r.test_id] = r
            continue
        earlier_fails = prev.rerun_failures + (prev.status in (FAILED, ERROR))
        r.rerun_failures += earlier_fails
        merged[r.test_id] = r
    return list(merged.values())


def parse_string(xml: str | bytes) -> list[TestResult]:
    try:
        root = SafeET.fromstring(xml)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise JUnitParseError(f"invalid JUnit XML: {exc}") from exc
    if root.tag not in ("testsuites", "testsuite"):
        raise JUnitParseError(f"unexpected root element <{root.tag}>")
    cases = [_parse_case(c) for c in root.iter("testcase")]
    return merge_attempts(cases)


def parse_file(path: str | Path) -> list[TestResult]:
    path = Path(path)
    try:
        return parse_string(path.read_bytes())
    except JUnitParseError as exc:
        raise JUnitParseError(f"{path}: {exc}") from exc


def parse_paths(paths: list[str | Path]) -> list[TestResult]:
    """Parse files and directories (recursively, *.xml)."""
    results: list[TestResult] = []
    for p in map(Path, paths):
        if not p.exists():
            raise JUnitParseError(f"no JUnit XML at {p} (did the test step run and write it?)")
        files = sorted(p.rglob("*.xml")) if p.is_dir() else [p]
        for f in files:
            results.extend(parse_file(f))
    return results
