"""In-memory stand-in for GitHubClient used by notifier and pipeline tests."""

import itertools


class FakeGitHub:
    def __init__(self):
        self.comments: dict[int, list[dict]] = {}
        self.issues: list[dict] = []
        self._ids = itertools.count(100)
        self.calls: list[str] = []

    def list_issue_comments(self, repo, number):
        return list(self.comments.get(number, []))

    def create_comment(self, repo, number, body):
        self.calls.append("create_comment")
        c = {"id": next(self._ids), "body": body}
        self.comments.setdefault(number, []).append(c)
        return c

    def update_comment(self, repo, comment_id, body):
        self.calls.append("update_comment")
        for cs in self.comments.values():
            for c in cs:
                if c["id"] == comment_id:
                    c["body"] = body
                    return c
        raise KeyError(comment_id)

    def list_issues(self, repo, labels, state="open"):
        return [i for i in self.issues if labels in i["labels"] and i["state"] == state]

    def get_issue(self, repo, number):
        return next(i for i in self.issues if i["number"] == number)

    def create_issue(self, repo, title, body, labels):
        self.calls.append("create_issue")
        n = next(self._ids)
        issue = {"number": n, "title": title, "body": body, "labels": labels, "state": "open",
                 "html_url": f"https://github.com/{repo}/issues/{n}",
                 "created_at": "2026-06-01T00:05:00Z"}
        self.issues.append(issue)
        return issue
