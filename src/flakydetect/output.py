"""Console output. Inside GitHub Actions, results and errors are also emitted as workflow
annotations so they can be read from the checks API without downloading logs."""

from __future__ import annotations

import os
import re
import sys

_URL_CREDS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s]+@", re.I)
_KV_SECRET = re.compile(r"\b(password|passwd|pwd|token|secret)(\s*[=:]\s*)(['\"]?)[^\s'\"&]+\3", re.I)


def redact(text: str) -> str:
    """Hide credentials in URLs (scheme://user:***@host) and key=value pairs."""
    text = _URL_CREDS.sub(r"\1***@", text)
    return _KV_SECRET.sub(r"\1\2\3***\3", text)


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def result(command: str, message: str) -> None:
    message = redact(message)
    print(message)
    if in_actions():
        print(f"::notice title=flakydetect {command}::{_escape(message)}")


def error(message: str) -> None:
    message = redact(message)
    print(f"error: {message}", file=sys.stderr)
    if in_actions():
        # Workflow commands are read from stdout.
        print(f"::error title=flakydetect::{_escape(message)}")
