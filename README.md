# Flaky Test Detector

A GitHub Action plus a small service that finds tests that pass and fail at random, tells
you on the PR when a failure is probably flaky, and opens an issue to quarantine the test.

## The problem

A red build should mean "your change broke something". Flaky tests break that promise:
the same code passes on one run and fails on the next because of timing, shared state,
test order, the network, random data or an async race. Developers learn to click
"Re-run jobs" until the build goes green. That wastes CI time, and it trains everyone to
ignore real failures.

This project records every test result from every CI run. It flags a test as flaky when
it **passes and fails on the same commit** or **passes only on a rerun**. From then on it
labels those failures on the PR, so nobody has to rerun to find out, and it opens a
quarantine issue so someone fixes the root cause.

## Measured results

Live report of the flakiest tests, rebuilt nightly:
**https://manaswichowdary.github.io/flaky-test-detector/**

<!-- metrics:start -->
| Metric | Result | Source |
|---|---|---|
| Planted flaky tests found | **6 of 6** | Live: `demo.yml` runs on GitHub (17 workflow runs, 49 test runs) |
| False alarms | **0** | Live: `demo.yml` runs on GitHub (17 workflow runs, 49 test runs) |
| Reruns avoided on the demo repo | **17 of 17** failed CI runs had only flaky failures | Live: `demo.yml` runs on GitHub (17 workflow runs, 49 test runs) |
| Time from a flaky failure to a quarantine issue | **median 0.3 min, max 0.7 min (6 issues)** | Live: failing run's completion time to the GitHub issue's `created_at` |
| Total CI test runs analyzed | **95** test runs from 63 workflow runs | Live: 49 demo test runs + 46 imported from 46 public workflow runs (sampled, below) |

Imported history is **sampled**: a stable 1-in-N subset of each suite's tests (the same tests in every run), because one full run of either suite is bigger than the 100k-results-per-repo budget. Full-suite sizes are estimates (sampled count × N).

| Repo | Workflow runs | Sampling | Tests analyzed (sampled) | Results analyzed (sampled) | ≈ Full suite |
|---|---|---|---|---|---|
| apache/druid | 30 | 1 in 120 tests | 2,533 | 75,973 | ≈ 303,960 tests, ≈ 303,892 results/run, ≈ 9,116,760 results |
| home-assistant/core | 16 | 1 in 40 tests | 2,627 | 33,887 | ≈ 105,080 tests, ≈ 84,718 results/run, ≈ 1,355,480 results |

<sub>Measured 2026-10-28 21:01 UTC from the project database (`flakydetect metrics`); raw numbers in `docs/metrics.json`.</sub>
<!-- metrics:end -->

How to read these numbers:

- **Live data:** every number above comes from the database that the GitHub Action fills
  during real `demo.yml` runs on this repo, plus the imported history. Run
  `flakydetect metrics --repo manaswichowdary/flaky-test-detector` against the same
  database to reproduce it.
- **The demo:** [`demo/tests`](demo/tests) has 30 tests, 6 of them flaky on purpose, and
  `demo.yml` runs it 3× per commit.
- **Reruns avoided:** a failed CI run counts as avoided when, by the time the run
  finished, every failure in it was already explained as flaky: the same test had passed
  on the same commit, or had earlier flaky evidence. The PR comment says so, and the
  `fail-on-real-failures` gate stays green, so nobody needs to click "Re-run".
- **Sample size:** 17 demo workflow runs is a small sample. These results show the
  pipeline working end to end, not a benchmark.
- **Refreshing:** every nightly run posts fresh numbers as annotations on its
  "Measure README metrics" step. To update the README, save that JSON as
  `docs/metrics.json` and regenerate the block between the `metrics` markers with
  `flakydetect.metrics.readme_block`. `tests/test_readme.py` fails if the two disagree.
- **History:** the imported history found **0** flaky tests in about 5,160 sampled tests.
  That's expected, because those projects run each commit once on their default branch,
  so there is no same-commit evidence. See [docs/history.md](docs/history.md), which also
  lists the measured full-suite sizes: 310,314 results in one apache/druid run and
  105,256 in one home-assistant/core run.

## One hard decision: warn, open an issue, or quarantine automatically?

Once a test is known to be flaky, what should the tool *do* about it?

- **`warn`**: comment on the PR only. Cheap, but nothing makes anyone fix the test.
- **`issue`** (default): comment on the PR **and** open one `flaky-test` issue per test,
  with the evidence (score, commits with both pass and fail, rerun passes). The test keeps
  running and can still fail the build.
- **`auto`**: also quarantine. Tests with an open issue are marked `xfail` (non-strict) by
  the pytest plugin, so they still run and report but can't fail the build. Closing the
  issue lifts the quarantine.

**Default: `issue`.** Auto-quarantine is tempting, but a "flaky" test is sometimes
catching a real race in the product code. Silencing it automatically hides exactly the
bugs that are hardest to reproduce. With `issue`, a human decides, and the PR comment
already removes the rerun reflex. The demo workflow adds the opt-in
`fail-on-real-failures` gate: known-flaky failures don't block the PR, unexplained ones do.

The other two decisions are configurable too and are explained under
[Configuration](#configuration).

## How it works

```
CI run finishes ─► Action uploads JUnit XML ─► collector stores one row per test per run
                                                     │
          PR comment / quarantine issue ◄── notify ◄─┴─► scorer updates every test
                                                     │
                     nightly HTML report ◄───────────┘
```

- **Collector** ([`collector.py`](src/flakydetect/collector.py)): parses JUnit XML (pytest,
  Maven Surefire including `flakyFailure`/`rerunFailure`, Gradle) and stores one row per
  test per run in Postgres. It is idempotent per run and attempt, and safe when parallel
  shards write at the same time.
- **Scorer** ([`scorer.py`](src/flakydetect/scorer.py)): evidence is a commit with both a
  pass and a fail, or a pass on rerun. The score is the decay-weighted share of commits
  with evidence (half-life 14 days, 30-day window).
- **Notifier** ([`notifier.py`](src/flakydetect/notifier.py)): keeps one PR comment per PR,
  updated in place, and opens de-duplicated quarantine issues.
- **Report** ([`report.py`](src/flakydetect/report.py)): a static page of the flakiest
  tests, built nightly and published to GitHub Pages.

## Setup

### 1. Create the database (Supabase free tier)

1. Create a project at [supabase.com](https://supabase.com).
2. Open **Connect**, choose **Session pooler**, and copy the URI. GitHub runners use IPv4;
   the direct connection is IPv6-only on the free tier.
3. Put in your database password and add `?sslmode=require` to the end:
   `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`

Tables are created on first use. The free tier is 500 MB; the history import stops at
300 MB (`max_db_mb`), and `flakydetect db-size` shows current usage.

### 2. Add the secret

**Settings → Secrets and variables → Actions → New repository secret**, named
`FLAKY_DATABASE_URL`. The Action refuses to run without it; it never falls back to a
throwaway SQLite file in CI. `GITHUB_TOKEN` is provided by Actions; no other token is
needed.

### 3. Add the Action to your test workflow

```yaml
permissions:
  issues: write
  pull-requests: write
  contents: read

jobs:
  test:
    strategy:
      matrix:
        shard: [1, 2, 3]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: pip install -e . pytest "git+https://github.com/manaswichowdary/flaky-test-detector@main"
      - run: pytest -p flakydetect.pytest_plugin --junitxml=junit/results.xml
        continue-on-error: true          # the notify job decides
      - uses: manaswichowdary/flaky-test-detector@main
        if: always()
        with:
          command: ingest
          junit-path: junit/results.xml
          database-url: ${{ secrets.FLAKY_DATABASE_URL }}
          run-key: ${{ github.run_id }}-test-${{ matrix.shard }}

  flaky-check:
    needs: test
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: manaswichowdary/flaky-test-detector@main
        with:
          command: notify
          database-url: ${{ secrets.FLAKY_DATABASE_URL }}
          fail-on-real-failures: "true"  # optional gate
```

Ingest in each test job, then notify once. A single job can use `command: all`. The
`-p flakydetect.pytest_plugin` flag adds body fingerprints for rename tracking and applies
quarantine in `auto` mode. It's optional; any JUnit XML works.

### 4. Nightly report (optional)

Enable **Settings → Pages → Source: GitHub Actions**. This repo's report is live at
<https://manaswichowdary.github.io/flaky-test-detector/>.
[`nightly.yml`](.github/workflows/nightly.yml) rescores every repo and publishes the report
from `main` each night. It also keeps the report as a workflow artifact.

### 5. Import history from other repos (optional)

List public repos that upload JUnit XML as Actions artifacts in
[`history.toml`](history.toml). [`history.yml`](.github/workflows/history.yml) runs
`discover-history` on the candidates, then `import-history` on the chosen repos.

## Configuration

Settings come from `flakydetect.toml` (`[flakydetect]` table) and are overridden by
`FLAKY_*` environment variables (for example `FLAKY_MODE=warn`).

| Hard decision | Setting | Default | Why |
|---|---|---|---|
| How many mixed results make a test flaky | `min_mixed_commits`, `min_rerun_passes`, `min_score`, `window_days`, `half_life_days`, `clear_after_clean_commits` | ≥1 commit with pass **and** fail, or ≥1 rerun pass; score ≥ 0.1; 30-day window; cleared after 10 clean commits in a row | A pass and a fail on the **same commit** is strong evidence because the code didn't change, so one is enough. The score floor stops one flip in hundreds of runs from flagging a test, and the clean-streak rule clears a test once it's fixed. A test that always fails is reported as broken, not flaky. |
| Quarantine automatically or only warn | `mode` = `warn` \| `issue` \| `auto` | `issue` | See [the hard decision](#one-hard-decision-warn-open-an-issue-or-quarantine-automatically) above. |
| Track a renamed or moved test | `rename_strategy` = `none` \| `heuristic` \| `map`, `aliases_file`, `rename_window_runs` | `heuristic` + `test_aliases.toml` | When a test ID disappears and a new one appears within 5 runs, they are linked if **exactly one** candidate has the same body fingerprint, or failing that the same function name. Explicit `[aliases]` in `test_aliases.toml` always win. Ambiguous cases stay separate: a missed link loses some history, while a wrong link would pin one test's flakiness on another. |

```toml
# flakydetect.toml
[flakydetect]
mode = "issue"
min_score = 0.1
rename_strategy = "heuristic"
```

## The demo suite

| Kind | Planted flaky test | Fixed version (with `ROOT_CAUSE` note) |
|---|---|---|
| Timing | `test_timing.py::test_warm_cache_is_fast` | [`test_timing_fixed.py`](demo/fixed/test_timing_fixed.py): measures the work, not the wall clock |
| Shared state | `test_cart.py::test_book_full_price` | [`test_cart_fixed.py`](demo/fixed/test_cart_fixed.py): no shared mutable default; state restored |
| Test order | `test_registry.py::test_lookup_csv_plugin` | [`test_registry_fixed.py`](demo/fixed/test_registry_fixed.py): each test arranges its own state |
| Network | `test_network.py::test_slow_endpoint_within_timeout` | [`test_network_fixed.py`](demo/fixed/test_network_fixed.py): deterministic latency in the test double |
| Random data | `test_text.py::test_slugify_random_title_is_nonempty` | [`test_text_fixed.py`](demo/fixed/test_text_fixed.py): explicit edge cases, seeded randomness |
| Async race | `test_async.py::test_concurrent_increments` | [`test_async_fixed.py`](demo/fixed/test_async_fixed.py): lock around read-modify-write |

The demo shuffles test order on every run (`DEMO_SEED` reproduces one).
`python -m pytest demo/meta -o addopts=` runs 100 shuffled sessions and checks two things:
the fixed tests never fail and score 0, and all 6 planted tests are flagged with no false
alarms.

## Development

```bash
pip install -e ".[dev]"
python -m pytest                                    # unit tests on SQLite (demo/ excluded)
TEST_POSTGRES_URL=postgresql+psycopg://... python -m pytest   # also against Postgres
python -m pytest demo/meta -o addopts=              # slow demo behaviour checks
flakydetect --help
```

The CLI commands are `ingest`, `score`, `notify`, `quarantine-list`, `discover-history`,
`import-history`, `report`, `metrics` and `db-size`. In GitHub Actions each prints its
result as a workflow annotation, and any connection string in an error is redacted.
Tokens only ever come from environment variables or secrets.
