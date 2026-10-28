"""Guard rail: no token-like strings may be committed anywhere in the repo."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),          # GitHub tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"postgres(ql)?(\+\w+)?://[^:\s/]+:(?!\*\*\*@|<[^>@\s]+>@)[^@\s]+@(?!localhost|127\.0\.0\.1|h[:/]|host)[\w.-]+"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]"),
]


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / f for f in out.stdout.splitlines()]


def test_no_hardcoded_tokens():
    hits = []
    for path in tracked_files():
        if not path.is_file() or path.suffix in {".png", ".zip"}:
            continue
        text = path.read_text(errors="ignore")
        for pattern in PATTERNS:
            for m in pattern.finditer(text):
                hits.append(f"{path.relative_to(ROOT)}: {m.group(0)[:40]}")
    assert not hits, "possible secrets committed:\n" + "\n".join(hits)


def test_patterns_catch_examples():
    fake = "ghp_" + "a" * 36
    assert PATTERNS[0].search(fake)
    assert PATTERNS[2].search("postgresql://user:" + "pw@db.example.supabase.co:5432/postgres")
    assert not PATTERNS[2].search("postgresql+psycopg://flaky:flaky@localhost:5432/flaky")
    assert not PATTERNS[2].search("postgresql://user:" + "***@db.example.com/d")
    assert not PATTERNS[2].search("postgresql://postgres.<ref>:" + "<password>@aws-0-x.pooler.supabase.com")
