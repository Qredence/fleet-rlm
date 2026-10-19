# CircleCI CI and release runbook

CircleCI project `rlm` for `Qredence/fleet-rlm` runs the required quality
pipeline from `.circleci/config.yml`. The current required pipeline definition
is `build-and-test` (`fe82e142-4105-421e-a525-eb47298d5e7a`).

## Manual runs

Install and authenticate the CircleCI CLI, then run commands from the
repository checkout. Manual CI and compatibility probes are restricted to
`main`:

```bash
circleci pipeline run --project gh/Qredence/fleet-rlm \
  --definition-id fe82e142-4105-421e-a525-eb47298d5e7a --branch main \
  --param run_ci=true

circleci pipeline run --project gh/Qredence/fleet-rlm \
  --definition-id fe82e142-4105-421e-a525-eb47298d5e7a --branch main \
  --param run_python314=true
```

The Python 3.14 probe is informational and does not change the supported
Python range (`>=3.11,<3.14`).

## Smart Deployments

CircleCI Project Settings for `rlm` currently reports Smart Deployments as
**Configured** (checked 2026-10-20). Treat this as project-level setup status;
it does not demonstrate that this repository's releases are being validated
against live monitoring signals.

The release workflow below publishes a Python library to TestPyPI and PyPI and
creates a GitHub release. It does not deploy a running Fleet service, define a
runtime environment, or provide an application rollback target. Do not add
deploy markers or release validation to package publication: registry upload
success cannot establish application health, and yanking a bad package remains
the documented recovery path.

Wire Smart Deployments into the CircleCI workflow that deploys a Fleet
application when that target exists. Select deploy tracking until a monitor
can send authenticated validation webhooks for the application's component,
environment, and version. Enable continuous validation only after confirming
those signals and reviewing the evaluation policy. Test the deploy marker,
validation result, and rollback path against a non-production target before
relying on automated rollback. CircleCI's [Smart Deployments setup guide](https://circleci.com/docs/guides/deploy/set-up-smart-deployments/) describes
the guided setup, monitor webhook requirements, and generated configuration.

## Smarter Testing

The required Python 3.12 job uses `.circleci/test-suites.yml` and
`circleci testsuite run "ci tests"`. It generates test impact data by running
the complete test suite on `main` and selects impacted tests on pull requests.
Changes to `pyproject.toml`, `uv.lock`, `Makefile`, `tests/**`, or
`.circleci/**` select all test atoms. Python 3.11 and 3.13 remain full-suite
runs so the supported-version matrix still tests every change.

After changing test discovery, JUnit output, coverage analysis, or selection
rules, run:

```bash
circleci testsuite doctor "ci tests"
```

If impact data is unavailable or the selection is suspect, run the full
Python 3.12 suite with:

```bash
circleci testsuite run "ci tests" --run-tests=all --analyze-tests=none
```

The `test-python-312` job uses dynamic test splitting, dynamic parallelism
with a maximum of two workers, and one automatic retry for a failed test atom.
Review test reruns in CircleCI Test Insights because a retry that passes makes
the job succeed and suppresses the original test error.

The Qredence Performance plan currently includes 10,000 paid Smarter Testing
test atoms each month. The current discovery command finds 37 file-level
atoms; a normal Python 3.12 test-suite run therefore counts 37 atoms even when
TIA selects only a subset. At 270 complete runs, 9,990 atoms are used. Check
the monthly atom total and forecast before increasing test volume. Disable
paid Smart Testing features when the forecast
would exceed 10,000 atoms. CircleCI config does not enforce a cumulative
monthly atom ceiling, so review usage before the threshold is reached; any
overage needs a separate decision.

## Package release

Before starting, confirm the version in `pyproject.toml` has not been published
and that the intended commit is on `main`. Set the requested version and the
full 40-character commit SHA to that exact `main` commit:

```bash
circleci pipeline run --project gh/Qredence/fleet-rlm \
  --definition-id fe82e142-4105-421e-a525-eb47298d5e7a --branch main \
  --param run_release=true \
  --param release_version=0.8.0 \
  --param release_sha=<full-main-commit-sha>
```

The required quality jobs complete first. The release then validates checkout
SHA and branch, builds the wheel and source archive once, writes a SHA-256
manifest, publishes to TestPyPI, downloads the candidate wheel from TestPyPI,
checks its digest against the build, and installs it in isolation. On success,
CircleCI publishes the same manifest-verified files to PyPI and creates the
GitHub release.

CircleCI contexts `fleet-testpypi-trusted-publishing` and
`fleet-pypi-trusted-publishing` must be restricted to the project, authorized
maintainers, `main`, and jobs without SSH reruns. Register distinct PyPI trusted
publishers for the project, CircleCI organization, pipeline definition, and
corresponding context; constrain the repository and `refs/heads/main`. The
`fleet-github-release` context must hold a narrowly scoped `GH_TOKEN` for this
repository. Do not add long-lived PyPI upload tokens.

## Migration and recovery

Keep `.github/workflows/quality.yml` active until CircleCI has a successful
pull-request run and `main` run. Confirm the CircleCI aggregate `quality` check
is visible, then require that observed status in branch protection. Only then
remove the GitHub Actions quality workflow. Verify PR synchronization and main
push triggers in the CircleCI GitHub App settings; a configuration file cannot
enable a missing project trigger. Preserve the separate Chunk pipeline and
disable obsolete deploy and setup pipeline definitions after checking their
consumers.

If a release stops after publishing to a registry, do not blindly rerun the
pipeline or overwrite a release. Inspect the registry's files and compare
SHA-256 digests with the CircleCI `release-distributions` artifact. Resume only
after confirming the uploaded files match. If an incorrect version was
published, yank it and publish a follow-up version; there is no automatic
rollback.

During initial adoption, record job queue and execution time, cache hit rate,
and CircleCI credit use. The Python 3.12 Smart Testing job is capped at two
workers; increase that cap only after measuring runtime and credit use.
