"""flakydetect: find flaky tests from JUnit results."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.exc import SQLAlchemyError

from . import config, db, ghenv, history, junit, notifier, output, scorer
from .collector import RunMeta, ingest
from .github_api import GitHubClient, GitHubError, MissingTokenError
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
        output.error("--repo, --run-id and --sha are required outside GitHub Actions")
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
    output.result("ingest", f"run {run_id} attempt {meta.run_attempt}: {state}")
    return 0


def cmd_score(args, settings) -> int:
    repo = args.repo or ghenv.run_metadata()["repo"]
    if not repo:
        output.error("--repo is required outside GitHub Actions")
        return 2
    engine = _engine(settings, args.require_persistent_db)
    scores = scorer.score_repo(engine, repo, settings)
    flaky = sorted((s for s in scores if s.is_flaky), key=lambda s: -s.score)
    lines = [f"scored {len(scores)} tests in {repo}: {len(flaky)} flaky"]
    lines += [f"  {s.score:5.2f}  mixed={s.mixed_commits} reruns={s.rerun_passes}  {s.test_id}"
              for s in flaky[: args.top]]
    output.result("score", "\n".join(lines))
    return 0


def cmd_notify(args, settings) -> int:
    gh = ghenv.run_metadata()
    repo, sha = args.repo or gh["repo"], args.sha or gh["sha"]
    pr = args.pr or gh["pr_number"]
    if not (repo and sha):
        output.error("--repo and --sha are required outside GitHub Actions")
        return 2
    engine = _engine(settings, args.require_persistent_db)
    out = notifier.notify(engine, GitHubClient(), settings, repo, sha, pr)
    flaky = sum(v.flaky for v in out.verdicts)
    lines = [f"{len(out.verdicts)} failing tests ({flaky} flaky); "
             f"issues opened: {len(out.issues_opened)}; comment: {out.comment_id or 'none'}"]
    lines += [f"  {'flaky' if v.flaky else 'real '}  {v.test_id}" for v in out.verdicts]
    output.result("notify", "\n".join(lines))
    return 0


def cmd_quarantine_list(args, settings) -> int:
    repo = args.repo or ghenv.run_metadata()["repo"]
    if args.require_persistent_db:
        config.require_persistent_db(settings)  # fail early even when the list is unused
    if settings.mode != "auto":
        return 0  # only auto mode skips tests
    engine = _engine(settings, args.require_persistent_db)
    for test_id in notifier.quarantined_tests(engine, repo):
        print(test_id)
    return 0


def cmd_discover_history(args, settings) -> int:
    cfg = history.load_config(args.history_config)
    repos = args.repos or cfg.candidates
    if not repos:
        output.error("no candidate repos (pass them or set candidates in history.toml)")
        return 2
    client = GitHubClient()
    for repo in repos:
        output.result("discover-history", history.discover(
            client, repo, cfg.artifact_pattern, cfg.sample_runs).summary())
    return 0


def cmd_import_history(args, settings) -> int:
    cfg = history.load_config(args.history_config)
    if args.max_runs:
        cfg.max_runs = args.max_runs
    repos = args.repos or cfg.repos
    if not repos:
        output.result("import-history", "no repos selected in history.toml; nothing to import")
        return 0
    client = GitHubClient()
    engine = _engine(settings, args.require_persistent_db)
    for repo in repos:
        res = history.import_repo(engine, client, repo, cfg)
        scores = scorer.score_repo(engine, repo, settings)
        flaky = sorted((s for s in scores if s.is_flaky), key=lambda s: -s.score)
        lines = [res.summary(), f"{repo}: {len(scores)} tests scored, {len(flaky)} flaky"]
        lines += [f"  {s.score:.2f} {s.test_id}" for s in flaky[:10]]
        lines += [f"  warning: {e}" for e in res.errors[:5]]
        output.result("import-history", "\n".join(lines))
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

    p = sub.add_parser("notify", help="comment on the PR and open quarantine issues")
    p.add_argument("--repo")
    p.add_argument("--sha")
    p.add_argument("--pr", type=int)
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("discover-history", help="find which public repos publish JUnit artifacts")
    p.add_argument("repos", nargs="*")
    p.add_argument("--history-config", default="history.toml")
    p.set_defaults(func=cmd_discover_history)

    p = sub.add_parser("import-history", help="import JUnit artifacts from public repos")
    p.add_argument("repos", nargs="*")
    p.add_argument("--history-config", default="history.toml")
    p.add_argument("--max-runs", type=int)
    p.set_defaults(func=cmd_import_history)

    p = sub.add_parser("quarantine-list", help="print quarantined test ids (auto mode only)")
    p.add_argument("--repo")
    p.set_defaults(func=cmd_quarantine_list)
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
    except (config.ConfigError, junit.JUnitParseError, MissingTokenError, GitHubError) as exc:
        output.error(str(exc))
        return 2
    except SQLAlchemyError as exc:
        # Keep the driver's message (e.g. "password authentication failed"), drop the SQL.
        detail = str(getattr(exc, "orig", None) or exc).strip().splitlines()
        output.error(f"database error ({type(exc).__name__}): {' '.join(detail[:3])}")
        return 2
    except Exception as exc:  # noqa: BLE001  (surface anything else as a readable annotation)
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        output.error(f"unexpected {type(exc).__name__} at {frame.filename.rsplit('/', 1)[-1]}:"
                     f"{frame.lineno}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
