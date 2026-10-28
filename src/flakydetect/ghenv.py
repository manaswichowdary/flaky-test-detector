"""Read run metadata from the GitHub Actions environment."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def run_metadata(env: dict[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    meta = {
        "repo": env.get("GITHUB_REPOSITORY"),
        "run_id": env.get("GITHUB_RUN_ID"),
        "run_attempt": int(env.get("GITHUB_RUN_ATTEMPT") or 1),
        "sha": env.get("GITHUB_SHA"),
        "branch": env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME"),
        "workflow": env.get("GITHUB_WORKFLOW"),
        "pr_number": None,
    }
    match = re.match(r"refs/pull/(\d+)/", env.get("GITHUB_REF", ""))
    if match:
        meta["pr_number"] = int(match.group(1))
    event_path = env.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        event = json.loads(Path(event_path).read_text())
        pr = event.get("pull_request")
        if pr:
            # Score on the PR head commit, not the ephemeral merge commit.
            meta["sha"] = pr["head"]["sha"]
            meta["pr_number"] = pr["number"]
    return meta
