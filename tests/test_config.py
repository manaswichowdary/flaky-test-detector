import pytest

from flakydetect import config
from flakydetect.config import ConfigError, Settings


def test_defaults(tmp_path):
    s = config.load(tmp_path / "missing.toml", env={})
    assert s == Settings()
    assert s.mode == "issue" and s.rename_strategy == "heuristic"
    assert s.db_url == config.LOCAL_DEFAULT_DB


def test_toml_then_env_override(tmp_path):
    cfg = tmp_path / "flakydetect.toml"
    cfg.write_text('[flakydetect]\nmode = "warn"\nmin_score = 0.3\nwindow_days = 7\n')
    s = config.load(cfg, env={"FLAKY_MIN_SCORE": "0.5"})
    assert s.mode == "warn" and s.min_score == 0.5 and s.window_days == 7


@pytest.mark.parametrize("env", [
    {"FLAKY_MODE": "nuke"},
    {"FLAKY_RENAME_STRATEGY": "guess"},
    {"FLAKY_MIN_SCORE": "2"},
    {"FLAKY_WINDOW_DAYS": "abc"},
])
def test_invalid_values_rejected(tmp_path, env):
    with pytest.raises(ConfigError):
        config.load(tmp_path / "x.toml", env=env)


def test_unknown_toml_key_rejected(tmp_path):
    cfg = tmp_path / "f.toml"
    cfg.write_text("[flakydetect]\ntypo_key = 1\n")
    with pytest.raises(ConfigError, match="typo_key"):
        config.load(cfg, env={})


@pytest.mark.parametrize("raw,expected", [
    ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ("postgresql://u:p@h/d?sslmode=require", "postgresql+psycopg://u:p@h/d?sslmode=require"),
    ("postgresql+psycopg://u@h/d", "postgresql+psycopg://u@h/d"),
    ("sqlite:///x.db", "sqlite:///x.db"),
])
def test_normalize_db_url(raw, expected):
    assert config.normalize_db_url(raw) == expected


def test_require_persistent_db():
    with pytest.raises(ConfigError, match="FLAKY_DATABASE_URL is not set"):
        config.require_persistent_db(Settings())
    with pytest.raises(ConfigError, match="must be a Postgres URL"):
        config.require_persistent_db(Settings(database_url="sqlite:///x.db"))
    url = config.require_persistent_db(Settings(database_url="postgres://u:p@h/d"))
    assert url.startswith("postgresql+psycopg://")
