"""Demo suite config: shuffles test order so order-dependent tests flake.

Set DEMO_SEED to reproduce an order; otherwise a random seed is used and printed.
"""

import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def pytest_collection_modifyitems(session, config, items):
    seed = int(os.environ.get("DEMO_SEED") or random.randrange(1_000_000))
    random.Random(seed).shuffle(items)
    config._demo_seed = seed


def pytest_report_header(config):
    return f"demo shuffle seed: {getattr(config, '_demo_seed', os.environ.get('DEMO_SEED', 'random'))}"
