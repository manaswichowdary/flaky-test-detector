"""Map raw test ids to a canonical test (rename/move tracking is added in step 4)."""

from __future__ import annotations

from .collector import Resolver, exact_resolver
from .config import Settings


def make_resolver(settings: Settings) -> Resolver:
    return exact_resolver
