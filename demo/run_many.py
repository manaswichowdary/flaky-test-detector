"""Run the demo suite N times, each with a fresh shuffle seed, writing JUnit XML per run.

    python demo/run_many.py --runs 20 --out junit-out
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_once(out: Path, index: int, seed: int, extra: list[str] | None = None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DEMO_SEED": str(seed)}
    cmd = [
        sys.executable, "-m", "pytest", str(ROOT / "demo" / "tests"), "-q",
        "-p", "no:cacheprovider", "-o", "addopts=",
        f"--junitxml={out / f'run-{index:04d}.xml'}", *(extra or []),
    ]
    return subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--out", type=Path, default=ROOT / "junit-out")
    parser.add_argument("--seed-base", type=int, default=0)
    args = parser.parse_args(argv)
    failed = sum(run_once(args.out, i, args.seed_base + i) != 0 for i in range(args.runs))
    print(f"{args.runs} runs, {failed} with failures, XML in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
