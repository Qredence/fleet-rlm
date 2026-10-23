# Contributing to Fleet RLM Lite

Start with the [README](README.md), [architecture](ARCHITECTURE.md), and
[repository guidelines](AGENTS.md). The application uses Python 3.11–3.13 and
uv; no Node toolchain is required.

## Setup and checks

```bash
uv sync --dev
uv run fleet --help
make check
make check-security
```

For focused changes, run the relevant tests with `uv run pytest -q <path>` and
the relevant Make targets. `make check` is the repository's non-live quality
gate. Ruff lint and format checks include `notebooks/`, matching the pre-commit
hooks. These checks do not execute notebook cells or live benchmarks. The quality
gate must not require Daytona, model-provider, or production credentials.
CircleCI splits the same non-live quality gate across static checks, complete
test runs on Python 3.11 and 3.13, a Smarter Testing pytest run on Python 3.12,
package build and smoke checks, and one locked dependency audit. The aggregate
required status is `quality`. The Python 3.12 run analyzes the complete suite
on `main` and selects impacted tests on pull requests; dependency, test, and
CircleCI configuration changes select the full suite.
GitHub Actions stays active during migration. Remove its workflow only after
CircleCI passes on a pull request and `main`, and branch protection requires
the observed CircleCI `quality` status. See the
[CircleCI CI and release runbook](docs/runbooks/circleci-ci-release.md).
The audit currently suppresses only `PYSEC-2026-2447` for transitive
`diskcache==5.6.3`: this package has no fixed upstream release, and the
advisory requires write access to its local cache directory. Remove this
narrow suppression when DSPy no longer brings in the affected package or a
fixed release becomes available. Other findings fail the audit.

The Python 3.14 probe is manual and informational. It installs dependencies
exported from `uv.lock` without installing Fleet itself, since Fleet declares
support as `>=3.11,<3.14`. In CircleCI, trigger the pipeline on `main` with
`run_python314=true`; its result does not gate supported-version CI. For a
manual CI run on `main`, set `run_ci=true`.

### CircleCI Smarter Testing

The Python 3.12 suite uses CircleCI test impact analysis, dynamic test
splitting, dynamic parallelism capped at two workers, and at most one
automatic retry per failed test atom. Python 3.11 and 3.13 continue to run the
complete suite on every CI pipeline. An automatic retry can turn an
intermittent test failure into a successful job, so review retry results in
CircleCI Test Insights rather than treating a green retry as proof the first
attempt passed.

CircleCI's Performance plan includes 10,000 paid Smarter Testing test atoms
per month. The current pytest discovery yields 37 file-level test atoms, and
each Python 3.12 `circleci testsuite run` counts all discovered atoms even
when TIA selects only some tests. That is 37 atoms per normal test-suite run;
270 complete runs use 9,990 atoms. Track the monthly total in CircleCI before
increasing the test suite or run volume. Keep paid Smart Testing features
disabled if the expected monthly total would exceed 10,000 atoms; exceeding
the included allowance requires a separate decision. Recount the discovered
files when tests are added or removed.

Run `circleci testsuite doctor "ci tests"` after changing
`.circleci/test-suites.yml`. For a manual complete Python 3.12 suite run, use
`circleci testsuite run "ci tests" --run-tests=all --analyze-tests=none`.
CircleCI selects new/unmapped test atoms and the full suite when files listed
under `full-test-run-paths` change; use the complete-run command if impact data
is unavailable or its selection looks stale.

## Guarded releases

Releases are operator-triggered on `main` only. Set `run_release=true`,
`release_version` to the version in `pyproject.toml`, and `release_sha` to the
full source commit SHA. The pipeline runs the quality and dependency audit,
checks the requested version, SHA, and `main` branch, builds and verifies the
wheel and source archive once, and records their SHA-256 checksums. It
publishes those artifacts to TestPyPI through CircleCI OIDC, downloads the
TestPyPI wheel and verifies it matches the built artifact, then publishes the
same checksummed files to PyPI and creates the versioned GitHub release.

The CircleCI project must have restricted contexts named
`fleet-testpypi-trusted-publishing` and `fleet-pypi-trusted-publishing`, each
configured as a PyPI trusted publisher for its respective registry. Restrict
them to this project, `main`, and non-SSH reruns. The
`fleet-github-release` context must expose a narrowly scoped `GH_TOKEN` that
can create repository tags and releases. Configure these once with CircleCI
and PyPI repository owner access. Bind each publisher to the CircleCI
organization, project, pipeline definition, registry-specific context,
repository, and `main` ref. Do not use long-lived PyPI upload tokens.

If a published PyPI release is bad, yank that release and publish a follow-up
version; the workflow deliberately has no automated rollback path.

Running the application itself requires PostgreSQL, Daytona API key,
organization ID, and Volume ID, plus model-provider configuration. See
[Configuration](docs/reference/configuration.md). Tables are created by the
application at startup; there is no migration command. Use only disposable or
authorized external resources for integration work.

## Making changes

1. Read the owning code and its tests. Current runtime owners are documented in
   [Architecture](ARCHITECTURE.md).
2. Keep changes focused and add regression tests for behavior changes.
3. Update the docs and API contract when public behavior changes.
4. Run focused tests, `make check` as appropriate, and `git diff --check`.
5. Preserve `.agents/skills/` as developer skills; user Skills live under
   `.fleet/skills/`.

## Pull requests

Use a conventional prefix such as `feat:`, `fix:`, `test:`, `docs:`, or
`chore:`. Summarize behavior, validation, and any external integration that
remains unverified. Never include credentials. Report security issues through
[SECURITY.md](SECURITY.md), not a public issue.
