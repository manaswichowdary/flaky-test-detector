"""pytest plugin.

- Adds a body `fingerprint` property to each test in the JUnit XML so renamed or moved
  tests can be linked to their history.
- In `auto` mode, tests listed in the quarantine file (FLAKY_QUARANTINE_FILE, written by
  `flakydetect quarantine-list`) still run but are marked xfail(strict=False), so they
  keep reporting results without failing the build.

Enable with:  pytest -p flakydetect.pytest_plugin --junitxml=results.xml
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import os
import textwrap
from pathlib import Path

import pytest


def fingerprint_source(source: str) -> str | None:
    """Hash of the function body, ignoring its name, docstring, comments and formatting."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return None
    func = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if func is None:
        return None
    body = func.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    dumped = "".join(ast.dump(stmt, annotate_fields=False) for stmt in body)
    return hashlib.sha1(dumped.encode()).hexdigest()[:16]


def junit_test_id(nodeid: str) -> str:
    """'pkg/test_m.py::Cls::test_x[1]' -> 'pkg.test_m.Cls::test_x[1]' (pytest's JUnit naming)."""
    parts = nodeid.split("::")
    module = parts[0].replace("/", ".").removesuffix(".py")
    return f"{'.'.join([module, *parts[1:-1]])}::{parts[-1]}"


def _quarantined(config) -> set[str]:
    path = config.getoption("flaky_quarantine") or os.environ.get("FLAKY_QUARANTINE_FILE")
    if not path or not Path(path).exists():
        return set()
    return {line.strip() for line in Path(path).read_text().splitlines() if line.strip()}


def pytest_addoption(parser):
    parser.addoption("--flaky-quarantine", dest="flaky_quarantine", default=None,
                     help="file of quarantined test ids (one per line)")


def pytest_collection_modifyitems(config, items):
    quarantined = _quarantined(config)
    for item in items:
        if quarantined and junit_test_id(item.nodeid) in quarantined:
            item.add_marker(pytest.mark.xfail(reason="quarantined flaky test", strict=False))
        func = getattr(item, "function", None)
        if func is None:
            continue
        try:
            fp = fingerprint_source(inspect.getsource(func))
        except (OSError, TypeError):
            fp = None
        if fp:
            callspec = getattr(item, "callspec", None)
            if callspec is not None:
                fp = hashlib.sha1(f"{fp}[{callspec.id}]".encode()).hexdigest()[:16]
            item.user_properties.append(("fingerprint", fp))
