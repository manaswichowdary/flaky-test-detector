"""Minimal GitHub REST client. The token always comes from the environment (GITHUB_TOKEN)."""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Iterator

import requests

DEFAULT_API = "https://api.github.com"


class MissingTokenError(RuntimeError):
    pass


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


class GitHubClient:
    def __init__(self, token: str | None = None, api_url: str | None = None,
                 session: requests.Session | None = None, max_retries: int = 3,
                 sleep=time.sleep):
        token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            raise MissingTokenError(
                "GITHUB_TOKEN is not set. In Actions pass `github-token: ${{ secrets.GITHUB_TOKEN }}`; "
                "locally export GITHUB_TOKEN."
            )
        self.api_url = (api_url or os.environ.get("GITHUB_API_URL") or DEFAULT_API).rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "flakydetect",
        })
        self.max_retries = max_retries
        self._sleep = sleep

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        for attempt in range(self.max_retries + 1):
            resp = self.session.request(method, url, timeout=30, **kwargs)
            retryable = resp.status_code in (429, 500, 502, 503, 504) or (
                resp.status_code == 403 and (
                    resp.headers.get("Retry-After") or resp.headers.get("X-RateLimit-Remaining") == "0"
                )
            )
            if not retryable or attempt == self.max_retries:
                break
            wait = resp.headers.get("Retry-After")
            if wait is None and resp.headers.get("X-RateLimit-Reset"):
                wait = max(int(resp.headers["X-RateLimit-Reset"]) - int(time.time()), 1)
            self._sleep(min(float(wait or 2 ** attempt), 60))
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise GitHubError(resp.status_code, message[:300])
        return resp

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, params=params or None).json()

    def paginate(self, path: str, key: str | None = None, max_pages: int = 100, **params) -> Iterator[dict]:
        params.setdefault("per_page", 100)
        url, page = path, 0
        while url and page < max_pages:
            resp = self.request("GET", url, params=params if page == 0 else None)
            data = resp.json()
            yield from (data[key] if key else data)
            url = resp.links.get("next", {}).get("url")
            page += 1

    # Issues and comments
    def list_issue_comments(self, repo: str, number: int) -> Iterator[dict]:
        return self.paginate(f"/repos/{repo}/issues/{number}/comments")

    def create_comment(self, repo: str, number: int, body: str) -> dict:
        return self.request("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}).json()

    def update_comment(self, repo: str, comment_id: int, body: str) -> dict:
        return self.request("PATCH", f"/repos/{repo}/issues/comments/{comment_id}", json={"body": body}).json()

    def list_issues(self, repo: str, labels: str, state: str = "open") -> Iterator[dict]:
        return self.paginate(f"/repos/{repo}/issues", labels=labels, state=state)

    def get_issue(self, repo: str, number: int) -> dict:
        return self.get(f"/repos/{repo}/issues/{number}")

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict:
        return self.request("POST", f"/repos/{repo}/issues",
                            json={"title": title, "body": body, "labels": labels}).json()

    # Actions (history import)
    def list_workflow_runs(self, repo: str, max_pages: int = 10, **params) -> Iterator[dict]:
        return self.paginate(f"/repos/{repo}/actions/runs", key="workflow_runs", max_pages=max_pages, **params)

    def list_run_artifacts(self, repo: str, run_id: int) -> Iterator[dict]:
        return self.paginate(f"/repos/{repo}/actions/runs/{run_id}/artifacts", key="artifacts")

    def download_artifact(self, repo: str, artifact_id: int) -> bytes:
        return self.request("GET", f"/repos/{repo}/actions/artifacts/{artifact_id}/zip").content
