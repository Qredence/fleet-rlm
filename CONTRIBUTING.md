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
gate. It must not require Daytona, model-provider, or production credentials.
CircleCI runs `make check` on Python 3.11, 3.12, and 3.13 and audits all
locked runtime and development dependencies once per pipeline. The aggregate
required status is `ci/circleci: quality`.
During migration, `.github/workflows/quality.yml` remains enabled until this
CircleCI configuration has passed on the branch and the project connection is
confirmed.
The audit currently suppresses only `PYSEC-2026-2447` for transitive
`diskcache==5.6.3`: this package has no fixed upstream release, and the
advisory requires write access to its local cache directory. Remove this
narrow suppression when DSPy no longer brings in the affected package or a
fixed release becomes available. Other findings fail the audit.

The Python 3.14 probe is manual and informational. It installs dependencies
exported from `uv.lock` without installing Fleet itself, since Fleet declares
support as `>=3.11,<3.14`. In CircleCI, trigger the pipeline on `main` with
`run_python314=true`; its result does not gate supported-version CI.

## Guarded releases

Releases are operator-triggered on `main` only. Set `run_release=true`,
`release_version` to the version in `pyproject.toml`, and `release_sha` to the
full source commit SHA. The pipeline runs the quality and dependency audit,
checks the requested version and SHA, builds and verifies the wheel and source
archive once, publishes to TestPyPI through CircleCI OIDC, and installs and
smoke-tests that exact package before publishing to PyPI and creating the
versioned GitHub release.

The CircleCI project must have restricted contexts named
`fleet-testpypi-trusted-publishing` and `fleet-pypi-trusted-publishing`, each
configured as a PyPI trusted publisher for its respective registry. Restrict
them to this project, `main`, and non-SSH reruns. The
`fleet-github-release` context must expose a narrowly scoped `GH_TOKEN` that
can create repository tags and releases. Configure these once with CircleCI
and PyPI repository owner access. Do not use long-lived PyPI upload tokens.

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
