import pytest
import responses

from flakydetect.github_api import GitHubClient, GitHubError, MissingTokenError, parse_time

API = "https://api.github.com"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    return GitHubClient(token="test-token", sleep=lambda s: None)


def test_missing_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(MissingTokenError, match="GITHUB_TOKEN is not set"):
        GitHubClient()


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    assert GitHubClient().session.headers["Authorization"] == "Bearer from-env"


@responses.activate
def test_pagination_follows_link_header(client):
    responses.get(f"{API}/repos/o/r/issues", json=[{"n": 1}],
                  headers={"Link": f'<{API}/repos/o/r/issues?page=2>; rel="next"'})
    responses.get(f"{API}/repos/o/r/issues?page=2", json=[{"n": 2}])
    assert [i["n"] for i in client.list_issues("o/r", labels="x")] == [1, 2]
    assert "labels=x" in responses.calls[0].request.url


@responses.activate
def test_paginate_with_key(client):
    responses.get(f"{API}/repos/o/r/actions/runs", json={"workflow_runs": [{"id": 5}]})
    assert [r["id"] for r in client.list_workflow_runs("o/r")] == [5]


@responses.activate
def test_retries_on_5xx_then_succeeds(client):
    responses.get(f"{API}/repos/o/r/issues/1", status=502)
    responses.get(f"{API}/repos/o/r/issues/1", json={"state": "open"})
    assert client.get_issue("o/r", 1)["state"] == "open"
    assert len(responses.calls) == 2


@responses.activate
def test_retries_on_rate_limit(client):
    responses.get(f"{API}/repos/o/r/issues/1", status=403, headers={"Retry-After": "1"},
                  json={"message": "secondary rate limit"})
    responses.get(f"{API}/repos/o/r/issues/1", json={"state": "closed"})
    assert client.get_issue("o/r", 1)["state"] == "closed"


@responses.activate
def test_gives_up_with_clear_error(client):
    responses.get(f"{API}/repos/o/r/issues/1", status=404, json={"message": "Not Found"})
    with pytest.raises(GitHubError, match="404: Not Found"):
        client.get_issue("o/r", 1)
    assert len(responses.calls) == 1  # 404 is not retried


@responses.activate
def test_create_issue_and_comment(client):
    responses.post(f"{API}/repos/o/r/issues", json={"number": 3})
    responses.post(f"{API}/repos/o/r/issues/3/comments", json={"id": 9})
    responses.patch(f"{API}/repos/o/r/issues/comments/9", json={"id": 9})
    assert client.create_issue("o/r", "t", "b", ["flaky-test"])["number"] == 3
    assert client.create_comment("o/r", 3, "hi")["id"] == 9
    assert client.update_comment("o/r", 9, "hi2")["id"] == 9
    assert b'"labels": ["flaky-test"]' in responses.calls[0].request.body


def test_parse_time():
    assert parse_time("2026-01-02T03:04:05Z").isoformat() == "2026-01-02T03:04:05"
    assert parse_time(None) is None


@responses.activate
def test_exhausted_rate_limit_fails_fast(client):
    import time as _time
    reset = str(int(_time.time()) + 3600)
    responses.get(f"{API}/repos/o/r/issues/1", status=403, json={"message": "API rate limit exceeded"},
                  headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})
    with pytest.raises(GitHubError, match="rate limit exhausted; resets at"):
        client.get_issue("o/r", 1)
    assert len(responses.calls) == 1  # no sleeping and retrying for an hour


@responses.activate
def test_short_rate_limit_wait_is_retried(client):
    import time as _time
    reset = str(int(_time.time()) + 5)
    responses.get(f"{API}/repos/o/r/issues/1", status=403, json={"message": "limit"},
                  headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})
    responses.get(f"{API}/repos/o/r/issues/1", json={"state": "open"})
    assert client.get_issue("o/r", 1)["state"] == "open"


@responses.activate
def test_rate_limit_summary(client):
    responses.get(f"{API}/rate_limit", json={"resources": {"core": {"remaining": 990, "limit": 1000, "reset": 0}}})
    assert client.rate_limit().startswith("API quota 990/1000, resets 00:00 UTC")
