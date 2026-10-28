"""flakydetect: find flaky tests from JUnit results."""

from __future__ import annotations

import argparse
import sys

from . import config, db, ghenv, junit, scorer
from .collector import RunMeta, ingest
from .identity import make_resolver


def _engine(settings: config.Settings, require_persistent: bool):
    url = config.require_persistent_db(settings) if require_persistent else settings.db_url
    engine = db.get_engine(url)
    db.init_db(engine)
    return engine


def cmd_ingest(args, settings) -> int:
    gh = ghenv.run_metadata()
    repo = args.repo or gh["repo"]
    run_id = args.run_id or gh["run_id"]
    sha = args.sha or gh["sha"]
    if not (repo and run_id and sha):
        print("error: --repo, --run-id and --sha are required outside GitHub Actions", file=sys.stderr)
        return 2
    results = junit.parse_paths(args.paths)
    meta = RunMeta(
        run_id=run_id, sha=sha, run_attempt=args.run_attempt or gh["run_attempt"],
        branch=args.branch or gh["branch"], pr_number=args.pr or gh["pr_number"],
        workflow=args.workflow or gh["workflow"], source=args.source,
    )
    engine = _engine(settings, args.require_persistent_db)
    out = ingest(engine, repo, meta, results, resolver=make_resolver(settings))
    state = "already ingested" if out.duplicate else f"stored {out.inserted} results"
    print(f"run {run_id} attempt {meta.run_attempt}: {state}")
    return 0


def cmd_score(args, settings) -> int:
    repo = args.repo or ghenv.run_metadata()["repo"]
    if not repo:
        print("error: --repo is required outside GitHub Actions", file=sys.stderr)
        return 2
    engine = _engine(settings, args.require_persistent_db)
    scores = scorer.score_repo(engine, repo, settings)
    flaky = sorted((s for s in scores if s.is_flaky), key=lambda s: -s.score)
    print(f"scored {len(scores)} tests in {repo}: {len(flaky)} flaky")
    for s in flaky[: args.top]:
        print(f"  {s.score:5.2f}  mixed={s.mixed_commits} reruns={s.rerun_passes}  {s.test_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flakydetect", description=__doc__)
    parser.add_argument("--config", help="path to flakydetect.toml")
    parser.add_argument("--require-persistent-db", action="store_true",
                        help="fail unless FLAKY_DATABASE_URL points at Postgres (use in CI)")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("ingest", help="store JUnit results for one CI run")
    p.add_argument("paths", nargs="+", help="JUnit XML files or directories")
    p.add_argument("--repo")
    p.add_argument("--run-id")
    p.add_argument("--run-attempt", type=int)
    p.add_argument("--sha")
    p.add_argument("--branch")
    p.add_argument("--pr", type=int)
    p.add_argument("--workflow")
    p.add_argument("--source", default="ci")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("score", help="recompute flakiness scores for a repo")
    p.add_argument("--repo")
    p.add_argument("--top", type=int, default=20)
    p.set_defaults(func=cmd_score)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        settings = config.load(args.config)
        return args.func(args, settings)
    except (config.ConfigError, junit.JUnitParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
