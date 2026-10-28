# Imported test history

History from two public projects that upload JUnit XML as GitHub Actions artifacts. It is
imported by [`history.yml`](../.github/workflows/history.yml) with the settings in
[`history.toml`](../history.toml). Reproduce the run with:

```bash
flakydetect discover-history    # check the candidate repos
flakydetect import-history      # import the repos listed in history.toml
flakydetect db-size
```

## Choosing the repos (discovery, 2026-10-28)

Twelve candidates were checked. Only two had JUnit artifacts on their default branch
(10 of 10 sampled runs each):

| Repo | Branch | Artifact | Size per run |
|---|---|---|---|
| home-assistant/core | `dev` | `test-results-full-*` (pytest, 10 shards) | ~1.7 MB |
| apache/druid | `master` | `unit-test-reports-*` (Maven Surefire) | ~57.6 MB |

apache/kafka had one run with JUnit (from its "Deflake a test" workflow). The rest
(apache/pulsar, apache/arrow, apache/shardingsphere, EnricoMi/publish-unit-test-result-action,
testcontainers/testcontainers-java, getsentry/sentry, python/mypy, grafana/k6,
spring-projects/spring-framework) publish no JUnit artifacts.

## Import (2026-10-28, `history.yml` job 123085373187)

A single run of either suite is bigger than the 100,000-results-per-repo budget
(apache/druid 310,314 results per run, home-assistant/core 105,256). So each repo is
**sampled**: a stable 1-in-N subset of tests chosen by a hash of the test ID. The same tests
are kept in every run, so each sampled test has its full history.

| Repo | Workflow runs | Sampling | Test results stored | Tests scored | Flaky found | Database after |
|---|---|---|---|---|---|---|
| home-assistant/core | 16 | 1 in 40 tests | 33,887 | 2,627 | 0 | 29.4 MB |
| apache/druid | 30 | 1 in 120 tests | 75,973 | 2,533 | 0 | 67.7 MB |

- Database size: 11.1 MB before, **67.7 MB** after (guard: 300 MB; Supabase free tier: 500 MB).
- home-assistant/core has 16 runs, not 30: only 16 of its last 60 completed default-branch
  CI runs uploaded the full-suite artifact.

## Why no flaky tests were found here

The scorer needs evidence: a **pass and a fail on the same commit**, or a **pass on
rerun**. Default-branch CI runs each commit once, and neither suite recorded reruns in the
sampled tests, so that evidence doesn't exist in this data. A failure followed by a pass
on a *later* commit is not counted, because the code changed in between.

This history therefore counts toward "CI test runs analyzed". It also shows the scorer
raising no false alarms across about 5,160 sampled tests from two large real-world suites,
which is what a conservative default should do. Flaky detection is demonstrated on the demo
repo, where `demo.yml` runs every commit 3 times.
