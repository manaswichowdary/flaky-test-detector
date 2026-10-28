"""Ground truth for the planted flaky tests, read from `# PLANTED-FLAKY: <kind>` markers."""

import ast
import re
from pathlib import Path

DEMO_TESTS = Path(__file__).resolve().parent / "tests"
_MARKER = re.compile(r"#\s*PLANTED-FLAKY:\s*([\w-]+)")


def _scan(directory: Path):
    for path in sorted(directory.glob("test_*.py")):
        lines = path.read_text().splitlines()
        tree = ast.parse("\n".join(lines))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                above = lines[node.lineno - 2] if node.lineno >= 2 else ""
                match = _MARKER.search(above)
                yield path.stem, node.name, (match.group(1) if match else None)


def all_tests(directory: Path = DEMO_TESTS) -> list[tuple[str, str]]:
    return [(module, name) for module, name, _ in _scan(directory)]


def planted(directory: Path = DEMO_TESTS) -> dict[str, str]:
    """Map 'module::test_name' to its flaky kind."""
    return {f"{m}::{n}": kind for m, n, kind in _scan(directory) if kind}
