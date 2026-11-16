# CI and releases

The checked-in [.circleci/config.yml](../../.circleci/config.yml) defines required
quality checks and manual release/recovery workflows. Validate configuration with
`make check-ci-config`; use `make check` for the offline quality gate.

## Run CI

Use the CircleCI plugin project slug
`circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf`.
Manual runs target `main` through the explicit build pipeline:

```bash
circleci api runs --method POST --data @- <<'JSON'
{
  "data": {
    "attributes": {
      "config": {"branch": "main"},
      "checkout": {"branch": "main"},
      "parameters": {"run_ci": true}
    },
    "references": {"pipeline": {"id": "fe82e142-4105-421e-a525-eb47298d5e7a"}}
  }
}
JSON
```

Python 3.11/3.13 run full suites on two workers. Combined 3.11 coverage enforces
80% and feeds SonarQube. Python 3.12 uses file-atom impact selection with per-file
LCOV; missing impact data selects all tests. Dependencies, fixtures, build/CI and
other runtime inputs force full selection. Keep `full-test-run-paths` current.
The aggregate `quality` gate requires tests, coverage and SonarQube.

For local impact diagnostics:

```bash
circleci testsuite doctor "ci tests"
circleci testsuite run "ci tests" --local --run-tests=all --analyze-tests=none
```

Retain failed-attempt reports; a pass on retry still needs investigation. Keep
the two-worker cap. Ordinary CI must not depend on live Fleet services.

## Publish an authorized release

1. Verify the version is unpublished, choose its full `main` SHA, and review the
   unique nonempty changelog section.
2. Use the run request above with these parameters:

   ```json
   {"run_release": true, "release_version": "<version>", "release_sha": "<full-sha>"}
   ```

3. Follow `quality → preflight → build once → TestPyPI → PyPI → canary + validation → GitHub`.
   Reuse verified distribution bytes and captured notes/requirements throughout.
4. Check public registry digests, successful isolated canary receipt, validation
   outcome, GitHub tag/notes/assets and deployment state. Retain workflow IDs and
   `dist/SHA256SUMS` evidence.

TestPyPI is enabled by default. If OIDC is unsupported, confirm no upload occurred
and obtain specific approval before using `skip_testpypi: true`. Publishing
credentials belong only in restricted purpose-specific contexts, never ordinary
CI, fork jobs, project-wide variables or task prompts.

The release validation window is 20 minutes with automatic rollback disabled.
A green window or accepted webhook alone does not prove package health: GitHub
publication also requires the successful public-PyPI canary receipt. Full workflow
cancellation can prevent finalizers; reconcile deployment state before retrying.

## Recover

For a correct published package with failed CI infrastructure, use the same run
request with:

```json
{"run_pypi_recovery": true, "release_version": "<published-version>", "release_sha": "<published-source-sha>"}
```

Recovery uploads nothing, reconstructs the exact source, verifies published
digests and opens separate validation. A mismatch blocks recovery. Registry files
cannot be overwritten; a defective package needs a corrected version and possibly
a manual yank. Conflicting/partial GitHub releases require inspection.

## Automation

[.circleci/setup.yml](../../.circleci/setup.yml) is the staged entry point;
`run_draft` defaults true and `activate_auto_release` false. Changing the production
entry point or enabling publication requires explicit authorization and isolated
acceptance with [.circleci/validation-acceptance.yml](../../.circleci/validation-acceptance.yml).
Keep configuration sourced from protected main and credentials inaccessible to
PR-controlled YAML. Use separate GitHub App installations/contexts for draft writes,
read-only metadata checks and publication. Require up-to-date branch protection.

Review `automation/release-draft`, classify changes with `release:*` labels, and
prepare the selected version with:

```bash
uv run --locked python -m scripts.release_automation prepare --version X.Y.Z
```

`release-ready` freezes updates. Refresh against current main before applying it;
rerun metadata checks after label/body edits. Disable draft/publication automation
to roll back while retaining manual release and no-upload recovery.

Chunk cloud tasks require explicit authorization and completed credential isolation.
Use the checked-in [repair](../../.circleci/tasks/ci-repair.md),
[release preparation](../../.circleci/tasks/release-preparation.md) and
[review checklist](../../.circleci/tasks/review-checklist.md). Tasks use a new branch
and draft PR; they do not merge or publish automatically.
