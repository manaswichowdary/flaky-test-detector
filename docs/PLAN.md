# Flaky Test Detector: Build Plan

## Context
The repo `manaswichowdary/flaky-test-detector` (cloned at `/home/user/flaky-test-detector`) is empty apart from a one-line README. We are building a GitHub Action plus a small Python service. It records test results per CI run, scores how flaky each test is, comments on PRs when a failure looks flaky, and opens quarantine issues. It also ships a demo suite with 6 tests that are flaky on purpose, a nightly HTML report and a README with measured results. Every step ends with passing tests and its own commit. Tokens only ever come from environment variables or GitHub secrets.

Work happens on a branch `build/flaky-detector`, pushed after each step. (Say so if you'd rather commit straight to `main`.)

## Architecture
```
CI run (pytest --junitxml) → Action uploads XML → collector (parse + store 1 row/test/run)
  → scorer (update test_scores) → notifier (PR comment | quarantine issue)
nightly workflow → report (static HTML) → GitHub Pages / artifact
```
Package `src/flakydetect/`:
- `config.py`: loads settings from `flakydetect.toml`, overridden by `FLAKY_*` env vars. Settings: thresholds, mode, rename strategy, DB URL.
- `db.py`: SQLAlchemy Core schema. `DATABASE_URL` uses SQLite by default and Postgres in CI/prod. Tables: `runs`, `test_results`, `tests` (canonical identity), `test_aliases`, `test_scores`, `quarantine_issues`.
- `junit.py`: parses JUnit XML in the pytest, Surefire and Gradle variants. Handles `<rerunFailure>`/`<flakyFailure>` elements and skipped/error cases.
- `identity.py`: maps test IDs to a canonical test and detects renames and moves.
- `collector.py`: ingests one run (repo, sha, branch, PR, run_id, run_attempt, timestamps, results). Idempotent on (run_id, attempt).
- `scorer.py`: computes the flakiness score.
- `github_api.py`: thin REST client over `requests` (token from `GITHUB_TOKEN`), with retry and pagination.
- `notifier.py`: builds the PR comment (upserted by a hidden marker) and quarantine issues (de-duplicated by label and test ID).
- `history.py`: imports JUnit artifacts from public repos.
- `report.py`: Jinja2 template → `report/index.html`.
- `metrics.py`: computes the README numbers.
- `cli.py`: `flakydetect ingest | score | notify | import-history | report | metrics`.

## Hard decisions (all configurable; the README explains each default)
1. **Flaky threshold.** Default: a test is flagged as flaky when, within the last 30 days, it has **≥1 commit with both a pass and a fail** (a "mixed commit") **or ≥1 rerun-pass** (it failed, then passed on rerun or `run_attempt` > 1), **and** score ≥ 0.1. Score = (mixed commits + rerun-passes) / commits observed, with older evidence weighted down (half-life 14 days). Config keys: `min_mixed_commits`, `min_score`, `window_days`. Reasoning: one mixed result on the same commit is strong evidence, because the code didn't change. The score floor prevents a test with one flip in 1,000 runs from being flagged.
2. **Quarantine mode**: `warn` | `issue` | `auto`. **Default `issue`**: comment on the PR and open a quarantine issue, but never skip the test automatically. `auto` adds the test to `quarantine.txt` and a conftest plugin then skips it. Reasoning: auto-skipping hides real regressions, so a human stays in the loop.
3. **Renamed or moved tests**: `none` | `heuristic` | `map`. **Default `heuristic` plus an optional `test_aliases.toml`**. When a test ID disappears and a new ID with the same function name or the same body hash (from the pytest plugin's `fingerprint` property) appears within N runs, the new ID is linked to the old one and keeps its history. Explicit map entries always win.

## Steps (each ends with green tests and a commit)

**Step 0: Scaffold.** Add `pyproject.toml` (deps: sqlalchemy, psycopg[binary], requests, jinja2, pytest, pytest-asyncio, playwright, responses), `src/` layout, `.gitignore`, and a CI workflow that runs pytest against SQLite and a Postgres service container.
*Tests:* a smoke import test; CI runs green.

**Step 1: Demo suite.** `demo/tests/` holds 30 tests: 24 stable and 6 flaky on purpose (timing/sleep threshold, shared module state, test order dependency, network via a local flaky HTTP server, unseeded random data, async race). Each flaky test is marked `# PLANTED-FLAKY: <kind>`. `demo/run_many.py` runs the suite N times with random ordering and writes JUnit files.
*Tests:* a meta-test checks there are 30 tests and 6 markers. Running the 24 stable tests 20 times gives 0 failures. Each planted test fails at least once in 50 forced runs.

**Step 2: JUnit parser.** `junit.py` with fixtures for pytest, Surefire (including `rerunFailure`) and a malformed file.
*Tests:* field extraction, status mapping, reruns, nested suites, bad XML gives a clear error.

**Step 3: DB + collector.** Schema, `ingest` CLI, one row per test per run, idempotent re-ingest.
*Tests:* round trip on SQLite in-memory; the same suite against Postgres when `TEST_POSTGRES_URL` is set (CI service); re-ingest creates no duplicates.

**Step 4: Identity / rename tracking.** `identity.py` plus a pytest plugin that writes a `fingerprint` property into the JUnit output.
*Tests:* a moved file keeps its history, a renamed function with the same body is linked, the explicit map overrides, `none` mode keeps IDs separate, unrelated tests are never merged.

**Step 5: Scorer.** Mixed commits, rerun-passes, decay and thresholds from config.
*Tests:* table-driven cases (always pass, always fail = broken not flaky, mixed on one commit, fail then pass on rerun, a fix commit followed by passes = not flaky, decay), plus threshold changes via config/env.

**Step 6: GitHub client + notifier.** PR comment upsert, quarantine issue create/dedupe, and the three modes. All HTTP is mocked with `responses`, and the token is read only from env.
*Tests:* comment body content, an existing comment gets updated, no duplicate issues, `warn` opens no issue, `auto` writes `quarantine.txt`, a missing token gives a clear error, and a grep test asserts no token-like strings appear in the repo.

**Step 7: GitHub Action.** A composite `action.yml` (inputs: database-url, mode, junit-path; uses `secrets.GITHUB_TOKEN` / `FLAKY_DATABASE_URL`) runs ingest → score → notify. `.github/workflows/demo.yml` runs the demo suite on every push/PR with a run matrix (to produce same-commit mixes) and calls the action.
*Tests:* an `act`-free integration test runs the CLI pipeline end to end on fixture XML with a mocked GitHub. A live check: a test PR on the repo gets a comment and an issue.

**Step 8: History import.** `history.py` lists workflow runs and downloads JUnit artifacts for 2 public repos. Candidates (verified during this step): repos that publish JUnit XML as Actions artifacts, e.g. apache/kafka or apache/arrow. Artifact downloads need a token (`GITHUB_TOKEN` from env). Imports are rate-limit aware and resumable.
*Tests:* mocked API pagination, zip extraction, skip of expired artifacts, resume. A manual run records counts in `docs/history.md`.

**Step 9: Fixed flaky tests.** `demo/fixed/` holds a fixed version of each of the 6 tests, each with a `ROOT_CAUSE` docstring (cause, symptom, fix).
*Tests:* each fixed test passes 100 runs in random order; the scorer reports a score of 0 for them.

**Step 10: Nightly HTML report.** `report.py` plus a template that lists the flakiest tests, score trend, last failures and issue links. `nightly.yml` runs on a schedule and publishes to Pages/artifact.
*Tests:* unit tests on the rendering data; **Playwright** tests open the generated HTML and check the table sorting, empty state and links.

**Step 11: Metrics + README.** `metrics.py` computes:
- planted flaky tests found out of 6, and false alarms (flagged tests that are not planted);
- reruns avoided: failed demo runs where every failure was already known to be flaky, so the PR comment told the developer not to rerun;
- time from a flaky failure to its quarantine issue: run `completed_at` → issue `created_at`, median and max;
- total CI test runs analyzed (demo plus imported history).

The README opens with the problem, the measured results and one hard decision (quarantine mode), then covers setup: secrets, DB URL, adding the action, and config.
*Tests:* metrics on a fixture DB give known numbers; a README check confirms the numbers in the README match the `metrics` output.

## Approved amendments
1. **Persistence:** the Action always uses a persistent Postgres database from the `FLAKY_DATABASE_URL` secret (Supabase). SQLite is only for local dev and unit tests. If the Action runs without the secret, it fails with a clear error and never silently falls back to SQLite. The README gets Supabase setup steps (pooler connection string, `sslmode=require`).
2. **Demo isolated from CI:** `ci.yml` runs `pytest --ignore=demo` (testpaths in pyproject also exclude `demo/`). Planted flaky tests run only in `demo.yml`. Step 1 meta-tests that execute the demo live in `demo/meta/` and run only in `demo.yml`/manually. CI runs only the fast structural check (30 tests, 6 markers).
3. **Permissions:** `demo.yml` and `nightly.yml` declare `permissions: { issues: write, pull-requests: write, contents: read }`. `ci.yml` uses `contents: read` only.
4. **Manual-step pauses:** I stop and ask you at each checklist item below, with exact instructions.
5. **Step 8 report:** before moving to Step 9, I report which 2 repos I chose and how many runs/test results I imported.

## Manual checklist (I pause and tell you at each point)
- [ ] **Before Step 7 live check:** create the Supabase project and send me the connection string. I won't need the URL value itself; you add it as the `FLAKY_DATABASE_URL` repo secret (Settings → Secrets and variables → Actions). I use it locally only if you choose to export it in this session.
- [ ] **Before Step 7 live check:** confirm Actions are enabled and the workflow permissions allow `GITHUB_TOKEN` to write issues and PRs.
- [ ] **Step 7:** open (or let me open) the test PR that triggers `demo.yml`, then confirm the comment and issue appear.
- [ ] **Step 8:** history import needs a token with public-repo read access to download artifacts. I'll use the session's `gh` auth or a `GITHUB_TOKEN` you export, never committed.
- [ ] **Step 10:** enable GitHub Pages (Settings → Pages → Source: GitHub Actions).
- [ ] **Step 11:** let `demo.yml` accumulate enough runs (I'll say how many) before taking the final measurements.

## Verification
- `pytest` (SQLite) and `TEST_POSTGRES_URL=... pytest` both pass locally and in CI.
- `playwright` tests pass against the generated report.
- End to end on GitHub: push the demo workflow, open a test PR, confirm the PR comment and a quarantine issue appear, and confirm the nightly report is published.
- `flakydetect metrics` output matches the README numbers.
