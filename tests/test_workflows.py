"""Static checks on the action and workflows (no network)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WRITE_PERMS = {"issues": "write", "pull-requests": "write", "contents": "read"}


def load(rel):
    return yaml.safe_load((ROOT / rel).read_text())


def test_action_inputs_and_steps():
    action = load("action.yml")
    assert action["runs"]["using"] == "composite"
    assert action["inputs"]["database-url"]["required"] is True
    for step in action["runs"]["steps"]:
        if "run" in step and "flakydetect" in step["run"]:
            assert "--require-persistent-db" in step["run"]
            assert "${{ inputs.database-url }}" not in step["run"]  # passed via env only


def test_ci_never_runs_demo():
    ci = load(".github/workflows/ci.yml")
    assert ci["permissions"] == {"contents": "read"}
    runs = [s.get("run", "") for s in ci["jobs"]["test"]["steps"]]
    pytest_steps = [r for r in runs if "pytest" in r]
    assert pytest_steps and all("--ignore=demo" in r for r in pytest_steps)


def test_commenting_workflows_have_permissions():
    for rel in [".github/workflows/demo.yml"]:
        assert load(rel)["permissions"] == WRITE_PERMS, rel


def test_demo_uses_secret_for_database():
    text = (ROOT / ".github/workflows/demo.yml").read_text()
    assert "secrets.FLAKY_DATABASE_URL" in text
    assert "postgres://" not in text and "postgresql://" not in text
