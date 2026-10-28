"""README numbers must match the measured metrics in docs/metrics.json."""

import json
import re
from pathlib import Path

from flakydetect.metrics import Metrics, readme_block

ROOT = Path(__file__).resolve().parents[1]


def test_readme_metrics_match_measured_numbers():
    readme = (ROOT / "README.md").read_text()
    block = re.search(r"<!-- metrics:start -->.*?<!-- metrics:end -->", readme, re.S).group(0)
    measured = Metrics.from_dict(json.loads((ROOT / "docs" / "metrics.json").read_text()))
    assert block == readme_block(measured)


def test_readme_opens_with_problem_results_decision_then_setup():
    headings = re.findall(r"^## (.+)$", (ROOT / "README.md").read_text(), re.M)
    assert headings[:3] == ["The problem", "Measured results",
                            "One hard decision: warn, open an issue, or quarantine automatically?"]
    assert headings.index("Setup") > 2
