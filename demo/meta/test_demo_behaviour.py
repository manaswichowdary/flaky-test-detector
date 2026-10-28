"""Behaviour checks for the demo suite. They execute the planted flaky tests, so they
run only in demo.yml or by hand, never in project CI:

    python -m pytest demo/meta -o addopts= -q
"""

import subprocess
import sys
from pathlib import Path

import pytest

from demo.planted import all_tests, planted

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "demo" / "tests"


def _run(node_ids: list[str], seed: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *node_ids, "-q", "-p", "no:cacheprovider", "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, env={"DEMO_SEED": str(seed), "PATH": ""},
    )


def _node(key: str) -> str:
    module, name = key.split("::")
    return f"{TESTS / module}.py::{name}"


def test_stable_tests_never_fail():
    flaky = set(planted())
    stable = [_node(f"{m}::{n}") for m, n in all_tests() if f"{m}::{n}" not in flaky]
    for seed in range(20):
        result = _run(stable, seed)
        assert result.returncode == 0, result.stdout[-2000:]


@pytest.mark.parametrize("key", sorted(planted()))
def test_planted_test_fails_sometimes(key):
    # Order-dependent tests need their partner in the same session.
    module = key.split("::")[0]
    targets = [str(TESTS / f"{module}.py")]
    failures = sum(
        key.split("::")[1] in _run(targets, seed).stdout for seed in range(50)
    )
    assert failures > 0, f"{key} never failed in 50 runs"
