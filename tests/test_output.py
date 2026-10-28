import pytest

from flakydetect import cli, output
from flakydetect.output import redact


@pytest.mark.parametrize("raw,expected", [
    # Built by concatenation so the repo secret scan doesn't flag these fake credentials.
    ("postgresql://postgres.abc:" + "s3cr%40t@pooler.example.com:5432/postgres?sslmode=require",
     "postgresql://postgres.abc:" + "***@pooler.example.com:5432/postgres?sslmode=require"),
    ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:***@h/d"),
    ("host=db user=me password=hunter2 sslmode=require", "host=db user=me password=*** sslmode=require"),
    ("token: 'abc123'", "token: '***'"),
    ('connection to server at "h" (1.2.3.4), port 5432 failed: FATAL: password authentication failed '
     'for user "postgres.abc"',
     'connection to server at "h" (1.2.3.4), port 5432 failed: FATAL: password authentication failed '
     'for user "postgres.abc"'),
    ("nothing secret here", "nothing secret here"),
])
def test_redact(raw, expected):
    assert redact(raw) == expected


def test_annotations_only_in_actions(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    output.result("score", "a\nb")
    assert "::notice" not in capsys.readouterr().out
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    output.result("score", "50% done\nnext")
    output.error("bad postgres://u:pw@h/d")
    out = capsys.readouterr()
    assert "::notice title=flakydetect score::50%25 done%0Anext" in out.out
    assert "::error title=flakydetect::bad postgres://u:***@h/d" in out.out
    assert "pw@" not in out.out + out.err


def test_unreachable_db_reports_redacted_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    # Nothing listens on port 1; the URL carries a fake password that must not leak.
    monkeypatch.setenv("FLAKY_DATABASE_URL", "postgresql://user:topsecret@127.0.0.1:1/db?connect_timeout=2")
    rc = cli.main(["--require-persistent-db", "score", "--repo", "o/r"])
    out = capsys.readouterr()
    assert rc == 2
    assert "::error title=flakydetect::database error (OperationalError)" in out.out
    assert "topsecret" not in out.out + out.err


def test_quarantine_list_requires_db_even_in_issue_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLAKY_DATABASE_URL", raising=False)
    assert cli.main(["--require-persistent-db", "quarantine-list", "--repo", "o/r"]) == 2
    assert "FLAKY_DATABASE_URL is not set" in capsys.readouterr().err
