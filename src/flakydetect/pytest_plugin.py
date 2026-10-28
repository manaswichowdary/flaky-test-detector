"""pytest plugin: adds a body `fingerprint` property to each test in the JUnit XML so
renamed or moved tests can be linked to their history.

Enable with:  pytest -p flakydetect.pytest_plugin --junitxml=results.xml
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap


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


def pytest_collection_modifyitems(items):
    for item in items:
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
