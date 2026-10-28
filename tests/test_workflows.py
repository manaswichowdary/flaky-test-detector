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
    cli_lines = [
        line.strip()
        for step in action["runs"]["steps"] if "run" in step
        for line in step["run"].splitlines() if line.strip().startswith("flakydetect ")
    ]
    assert len(cli_lines) == 3
    for line in cli_lines:
        assert "--require-persistent-db" in line
    for step in action["runs"]["steps"]:
        assert "${{ inputs.database-url }}" not in step.get("run", "")  # passed via env only


def test_ci_never_runs_demo():
    ci = load(".github/workflows/ci.yml")
    assert ci["permissions"] == {"contents": "read"}
    runs = [s.get("run", "") for s in ci["jobs"]["test"]["steps"]]
    pytest_steps = [r for r in runs if "pytest" in r]
    assert pytest_steps and all("--ignore=demo" in r for r in pytest_steps)


def test_commenting_workflows_have_permissions():
    for rel in [".github/workflows/demo.yml"]:
        assert load(rel)["permissions"] == WRITE_PERMS, rel


def test_demo_job_installs_pytest():
    steps = load(".github/workflows/demo.yml")["jobs"]["demo"]["steps"]
    installs = [s["run"] for s in steps if s.get("run", "").startswith("pip install")]
    assert installs and "pytest" in installs[0]


def test_demo_uses_secret_for_database():
    text = (ROOT / ".github/workflows/demo.yml").read_text()
    assert "secrets.FLAKY_DATABASE_URL" in text
    assert "postgres://" not in text and "postgresql://" not in text


def test_history_workflow():
    wf = load(".github/workflows/history.yml")
    assert wf["permissions"] == {"contents": "read", "actions": "read"}
    on = wf[True] if True in wf else wf["on"]  # PyYAML parses "on" as True
    assert "history.toml" in on["push"]["paths"]
    text = (ROOT / ".github/workflows/history.yml").read_text()
    assert "secrets.FLAKY_DATABASE_URL" in text and "--require-persistent-db import-history" in text
    assert "postgres://" not in text and "postgresql://" not in text
