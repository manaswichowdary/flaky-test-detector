"""Settings: defaults < flakydetect.toml ([flakydetect] table) < FLAKY_* environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

MODES = ("warn", "issue", "auto")
RENAME_STRATEGIES = ("none", "heuristic", "map")
LOCAL_DEFAULT_DB = "sqlite:///flakydetect.db"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    # Flaky threshold (hard decision 1)
    min_mixed_commits: int = 1
    min_rerun_passes: int = 1
    min_score: float = 0.1
    window_days: int = 30
    half_life_days: float = 14.0
    # Quarantine mode (hard decision 2)
    mode: str = "issue"
    quarantine_file: str = "quarantine.txt"
    issue_label: str = "flaky-test"
    # Rename tracking (hard decision 3)
    rename_strategy: str = "heuristic"
    aliases_file: str = "test_aliases.toml"
    rename_window_runs: int = 5

    def validate(self) -> "Settings":
        if self.mode not in MODES:
            raise ConfigError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.rename_strategy not in RENAME_STRATEGIES:
            raise ConfigError(f"rename_strategy must be one of {RENAME_STRATEGIES}, got {self.rename_strategy!r}")
        if not 0 <= self.min_score <= 1:
            raise ConfigError("min_score must be between 0 and 1")
        if self.window_days <= 0 or self.half_life_days <= 0:
            raise ConfigError("window_days and half_life_days must be positive")
        return self

    @property
    def db_url(self) -> str:
        return normalize_db_url(self.database_url or LOCAL_DEFAULT_DB)


def normalize_db_url(url: str) -> str:
    """Accept the postgres:// URLs Supabase/Heroku hand out and use the psycopg3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _coerce(value: str, typ):
    if typ in (int, "int"):
        return int(value)
    if typ in (float, "float"):
        return float(value)
    return value


def load(path: str | Path | None = None, env: dict[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    values: dict = {}
    path = Path(path or env.get("FLAKY_CONFIG", "flakydetect.toml"))
    if path.exists():
        with path.open("rb") as fh:
            values.update(tomllib.load(fh).get("flakydetect", {}))
    for f in fields(Settings):
        key = "FLAKY_" + f.name.upper()
        if key in env and env[key] != "":
            values[f.name] = env[key]
    known = {f.name: f.type for f in fields(Settings)}
    unknown = set(values) - set(known)
    if unknown:
        raise ConfigError(f"unknown settings: {sorted(unknown)}")
    try:
        coerced = {k: _coerce(v, known[k]) if isinstance(v, str) else v for k, v in values.items()}
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    return replace(Settings(), **coerced).validate()


def require_persistent_db(settings: Settings) -> str:
    """CI must write to the persistent Postgres DB, never to a throwaway SQLite file."""
    if not settings.database_url:
        raise ConfigError(
            "FLAKY_DATABASE_URL is not set. Add it as a repository secret "
            "(Settings → Secrets and variables → Actions) and pass it to the action."
        )
    url = settings.db_url
    if not url.startswith("postgresql"):
        raise ConfigError("FLAKY_DATABASE_URL must be a Postgres URL when running in CI")
    return url
