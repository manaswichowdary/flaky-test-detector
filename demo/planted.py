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


DEMO_FIXED = Path(__file__).resolve().parent / "fixed"
_FIXES = re.compile(r"#\s*FIXES:\s*([\w-]+)\s*\(([^)]+)\)")


def fixes(directory: Path = DEMO_FIXED) -> dict[str, dict]:
    """Map flaky kind -> {"original", "fixed", "root_cause"} from `# FIXES:` markers."""
    out = {}
    for path in sorted(directory.glob("test_*.py")):
        source = path.read_text()
        lines = source.splitlines()
        tree = ast.parse(source)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            # The marker sits above the decorators (or the def when there are none).
            first = min([d.lineno for d in node.decorator_list] + [node.lineno])
            match = _FIXES.search(lines[first - 2]) if first >= 2 else None
            if match:
                out[match.group(1)] = {
                    "original": match.group(2),
                    "fixed": f"{path.stem}::{node.name}",
                    "root_cause": ast.get_docstring(node) or "",
                }
    return out
