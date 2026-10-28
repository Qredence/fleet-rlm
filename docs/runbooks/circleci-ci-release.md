# CircleCI CI and release runbook

CircleCI project `rlm` for `Qredence/fleet-rlm` runs the required quality
pipeline from `.circleci/config.yml`. Its CircleCI project slug is
`circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf`. The current required
pipeline definition is `build-and-test`
(`fe82e142-4105-421e-a525-eb47298d5e7a`).

## Manual runs

Install and authenticate the CircleCI CLI, then run commands from the
repository checkout. Manual CI and compatibility probes are restricted to
`main`:

```bash
circleci pipeline run --project circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf \
  --definition-id fe82e142-4105-421e-a525-eb47298d5e7a --branch main \
  --param run_ci=true

circleci pipeline run --project circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf \
  --definition-id fe82e142-4105-421e-a525-eb47298d5e7a --branch main \
  --param run_python314=true
```

The Python 3.14 probe is informational and does not change the supported
Python range (`>=3.11,<3.14`).

## SonarQube Cloud

The `sonarqube` job analyzes `Qredence_fleet-rlm` in the `qredence` organization
on SonarQube Cloud EU. It uses the official SonarScanner CLI 8.1.0.6389 Linux
x64 distribution, verifies its pinned SHA-256, and waits up to five minutes for
the server quality gate. The aggregate `quality` job, and therefore release
preflight, requires this job to pass.

Python 3.11's existing full-suite job produces `coverage/coverage.xml` and
shares only that report through the workflow workspace. Coverage.py uses
relative file paths so the scanner can resolve the report in its own checkout.
The Python 3.12 selective tests and Python 3.13 full suite keep their existing
behavior; SonarQube does not use partial or merged matrix coverage. Runtime
and repository scripts are analyzed, while coverage measures the shipped
`src/fleet_rlm` package. `sonar-project.properties` declares this scope and the
supported Python versions explicitly, with scanner automatic configuration
disabled.

Only the analysis job requests the existing `SonarCloud` context and its
`SONAR_TOKEN`. The context is restricted to project `rlm`, excludes SSH reruns,
and rejects API-supplied configuration. Fork PR jobs do not receive secrets.
Keep the organization's uncertified public-orb setting disabled: this job runs
the official scanner directly and needs no third-party orb permission.

For the initial migration, finish local validation and obtain authorization to
publish the reviewed configuration. Immediately before the first push that
uses it, turn off **Automatic analysis** in the SonarQube project's analysis
settings. CI analysis and automatic analysis cannot run together; leaving
automatic analysis on for the integration PR would fail its required scan.
Then publish the configuration, run the existing `run_ci=true` pipeline on
`main`, and confirm the `sonarqube` job imports coverage and reports a
quality-gate result. Until ready to publish, retain automatic analysis so the
project continues to receive scans. If publication or activation fails, restore
automatic analysis while reverting or repairing the CI integration.

The [SonarScanner CLI documentation](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/scanners/sonarscanner-cli)
describes scanner setup, and the
[Python coverage guide](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/test-coverage/python-test-coverage)
describes coverage import.

## Chunk tasks

The Chunk CLI project configuration provides local validations through
`chunk validate`. Run all configured checks together, or select an individual
check:

```bash
chunk validate
chunk validate static
chunk validate tests
chunk validate build
```

The `dev` task definition uses the existing `build-and-test` CircleCI pipeline.
Cloud tasks use committed repository code on the selected branch; local
uncommitted changes are not included. To start a task, provide an explicit
prompt and branch:

```bash
chunk task run --definition dev --branch main \
  --prompt "<describe the requested change and acceptance criteria>"
```

The default Chunk environment is configured in
`.circleci/cci-agent-setup.yml`. It installs the locked development
dependencies for Python 3.12 and does not run tests; use Chunk validation for
that. Chunk is currently in beta and task runs consume CircleCI credits and
model-provider tokens.

## Smart Deployments

CircleCI Project Settings for `rlm` currently reports Smart Deployments as
**Configured** (checked 2026-10-20). Treat this as project-level setup status;
the project has existing `fleet-rlm` / `pypi` deployment history, and the
release workflow now adds package-specific validation. A running Fleet service
is not the target: the canary verifies that the published Python package can be
downloaded and used by a consumer.

The `publish-pypi` job plans a deployment named `fleet-rlm-pypi` for component
`fleet-rlm`, environment `pypi`, and the requested release version. After the
PyPI upload succeeds, it marks that deployment `running`. The typed
`release-validation` job opens a 20-minute validation window while
`pypi-canary` downloads the exact version exclusively from `https://pypi.org/simple`,
checks its wheel digest against `dist/SHA256SUMS`, installs the locked runtime
dependencies, and runs the package CLI/API smoke checks in isolation. The
canary retries PyPI visibility for a bounded number of attempts and its total
work is capped at 15 minutes so a failed check can report during the
validation window. GitHub release creation requires both the canary job and
successful CircleCI release validation.

The canary emits a `pypi_canary` custom-provider failure signal only when the
download, digest verification, or smoke check fails. The signal includes the
matching component, environment, and version, and the canary job also fails so
the direct GitHub release dependency blocks even if validation delivery is
misconfigured. A successful canary emits no failure signal and the job passes.
If CircleCI cannot accept a failure signal, the canary job fails and blocks the
GitHub release.
CircleCI's [release validation guide](https://circleci.com/docs/guides/deploy/set-up-release-validation-manually/)
requires the signal name to match the configured webhook and the component,
environment, and version to match the planned deployment.

Before running a release, create the CircleCI Deploys validation webhook and
add its token as `CIRCLECI_VALIDATION_WEBHOOK_TOKEN` in the
`fleet-pypi-validation-webhook` context. The context is restricted to the
`rlm` project, `main`, pipelines that are not API-triggered, and jobs that are
not SSH reruns. CircleCI combines these restrictions, so all must pass. The
`validate-pypi-webhook-context` job checks for the credential before any
package publication. Never put the webhook token in the repository, a
project-wide environment variable, or a job log.

Automatic rollback is explicitly disabled for this release job. PyPI package
uploads cannot be undone by the configured CircleCI `rollback-pipeline`; if
the canary fails after upload, do not rerun or overwrite the version. Inspect
the deploy record and PyPI files, manually yank the defective release, then
publish a corrected follow-up version. CircleCI validation and deployment
markers are described in its [Smart Deployments overview](https://circleci.com/docs/guides/deploy/smart-deployments-overview/)
and [configuration reference](https://circleci.com/docs/guides/deploy/release-validation-reference/).

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

Release artifact verification, registry downloads, and smoke checks accept one
version such as `0.8.0` or `0.8.1rc1`. They reject wildcards, requirement markers,
paths, and command options before running pip or contacting the registry.

```bash
circleci pipeline run --project circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf \
  --definition-id fe82e142-4105-421e-a525-eb47298d5e7a --branch main \
  --param run_release=true \
  --param release_version=0.8.0 \
  --param release_sha=<full-main-commit-sha>
```

The TestPyPI rehearsal is enabled by default. When the TestPyPI service cannot
accept CircleCI OIDC tokens, a maintainer may explicitly request a direct
PyPI-only publication by adding `--param skip_testpypi=true`. This skips only
the TestPyPI upload and smoke test; it retains the quality gate, SHA preflight,
single manifest-verified build, PyPI-only canary, validation, and gated GitHub
release. Use this path only after reviewing the extra risk of publishing
without the TestPyPI rehearsal.

The required quality jobs complete first. The release then validates checkout
SHA and branch, builds the wheel and source archive once, writes a SHA-256
manifest, publishes to TestPyPI, downloads the candidate wheel from TestPyPI,
checks its digest against the build, and installs it in isolation. It then
plans and publishes the same manifest-verified files to PyPI, marks the package
release as running in CircleCI, and runs the PyPI-only canary during release
validation. CircleCI creates the GitHub release only after validation and the
canary both pass.

The `publish-pypi` job uses the `fleet-pypi-upload-token` CircleCI context. Add
`PYPI_API_TOKEN` there using a PyPI API token scoped only to the `fleet-rlm`
project. Restrict this context to the `rlm` project, `main`, non-API pipeline
triggers, and jobs without SSH reruns; the workflow attaches it only to
`publish-pypi`. The token is passed to Twine through its standard
`TWINE_USERNAME=__token__` and `TWINE_PASSWORD` variables and is never written
to the repository or printed. Revoke and replace it in PyPI and CircleCI if it
is exposed. This long-lived token is the fallback for CircleCI's currently
unsupported PyPI trusted-publishing issuer; remove it only after a verified
OIDC release path is available.

The `fleet-testpypi-trusted-publishing` context remains reserved for TestPyPI
OIDC. The `fleet-github-release` context must hold a narrowly scoped `GH_TOKEN`
for this repository. The `fleet-pypi-validation-webhook` context must contain
the CircleCI-generated `CIRCLECI_VALIDATION_WEBHOOK_TOKEN` and be restricted to
the `rlm` project, `main`, non-API pipeline triggers, and jobs without SSH
reruns.

If TestPyPI rejects token exchange with `unknown trusted publishing issuer`,
the registry does not recognize CircleCI's OIDC provider. That happens before
any upload. Confirm the requested version is absent from TestPyPI and run the
release with `--param skip_testpypi=true` only after reviewing the added risk of
omitting that rehearsal. This flag skips only TestPyPI upload and smoke; it does
not skip the PyPI artifact checksum, isolated PyPI canary, release validation,
or GitHub release gate. A PyPI upload failure before acceptance leaves the
version unpublished. If a canary fails because the published package is
defective, inspect the deploy record and PyPI files, manually yank the release,
then publish a corrected follow-up version. Do not rerun the normal release
pipeline for a version already on PyPI.

If publication succeeded but the canary job failed before inspecting the
artifact (for example, an interpreter or CI setup error), use the no-upload
recovery workflow. It is restricted to `main`, requires the published source
SHA to be an ancestor of the current commit, rebuilds from that exact source
SHA, and verifies both PyPI file digests against the rebuilt manifest. The
canary retries transient PyPI metadata errors and file-visibility delays within
its deadline; an actual checksum mismatch fails immediately. The recovery has
no registry upload job; it opens the validation window alongside the fresh
canary before creating the GitHub release. For the 0.8.0 recovery, use the SHA
that produced the published files:

```bash
circleci pipeline run --project circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf \
  --definition-id fe82e142-4105-421e-a525-eb47298d5e7a --branch main \
  --param run_pypi_recovery=true \
  --param release_version=0.8.0 \
  --param release_sha=a8163c3564b4561b9258af97f16e2a7e0f76c8ae
```

The recovery creates a separate deployment record, so its validation result is
visible independently from the first attempt. If the rebuild does not match all
published PyPI hashes, it stops before creating a GitHub release.

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
