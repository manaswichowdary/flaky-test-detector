"""Fixed tests never fail across 100 shuffled runs alongside the buggy suite, and the
scorer gives them 0 while still flagging every planted test. Runs only in demo.yml / by hand:

    python -m pytest demo/meta -o addopts= -q
"""

import subprocess
import sys
from pathlib import Path

from flakydetect import db
from flakydetect.collector import RunMeta, ingest
from flakydetect.config import Settings
from flakydetect.junit import ERROR, FAILED, parse_file
from flakydetect.scorer import score_repo

from demo.planted import planted

ROOT = Path(__file__).resolve().parents[2]
RUNS = 100


def test_fixed_tests_stable_and_scored_zero(tmp_path):
    for seed in range(RUNS):
        subprocess.run(
            [sys.executable, "-m", "pytest", "demo/tests", "demo/fixed", "-q", "-p", "no:cacheprovider",
             "-o", "addopts=", f"--junitxml={tmp_path / f'run-{seed:03d}.xml'}"],
            cwd=ROOT, capture_output=True, env={"DEMO_SEED": str(seed), "PATH": ""},
        )
    engine = db.get_engine(f"sqlite:///{tmp_path / 'f.db'}")
    db.init_db(engine)
    fixed_failures = []
    for seed in range(RUNS):
        results = parse_file(tmp_path / f"run-{seed:03d}.xml")
        fixed_failures += [r.test_id for r in results
                           if r.test_id.startswith("demo.fixed.") and r.status in (FAILED, ERROR)]
        ingest(engine, "demo", RunMeta(run_id=str(seed), sha="one-commit"), results)
    assert fixed_failures == []

    scores = {s.test_id: s for s in score_repo(engine, "demo", Settings())}
    fixed = {k: v for k, v in scores.items() if k.startswith("demo.fixed.")}
    assert len(fixed) >= 6 and all(s.score == 0 and not s.is_flaky for s in fixed.values())
    flagged = {k.removeprefix("demo.tests.") for k, s in scores.items() if s.is_flaky}
    assert flagged == set(planted())  # all 6 planted, no false alarms
