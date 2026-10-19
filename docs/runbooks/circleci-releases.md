# CircleCI validation and package releases

## Current implementation and activation status

The repository implements secret-free tests on Python 3.11–3.13, one Python 3.12
static/build job, a locked dependency audit, JUnit reports, uv download caching,
and an aggregate `quality` gate. Python 3.14 remains a manual informational probe.
The executor sets `UV_PYTHON` so the developer `.python-version` cannot silently
replace the matrix interpreter. Main validation commands have a 30-minute limit.

The release workflow is manual on `main`: source/version preflight, one build,
TestPyPI publication, exact-artifact CLI smoke tests on all supported Pythons,
`approve-pypi`, PyPI publication, the same smoke matrix, and GitHub finalization.
Ordinary pushes, PR events, and tags cannot publish.

The PyPI publisher creates a CircleCI deploy marker in the existing `pypi`
environment for component `fleet-rlm`, using the explicit release version as its
target. It marks publication running while upload and exact-hash registry
verification execute, then records success or failure. A failure marker does not
yank or remove a PyPI release. CircleCI's restored "live version" is timeline
metadata only and does not change what users can install. The marker is deliberately
in the same job as the publisher so status and upload share one workflow execution.

As of 2026-10-19, repository validation is separate from account activation:

| Item | Verified state |
| --- | --- |
| Organization UUID | `d0fc62ae-1f4a-4037-8ccb-d357adfdfda6` |
| Project UUID | `122b2cc9-5a9c-4b3b-88c6-9276bd5d5d98` (`rlm`) |
| Canonical definition | `build-and-test`, `fe82e142-4105-421e-a525-eb47298d5e7a` |
| Config and checkout | `Qredence/fleet-rlm`, `.circleci/config.yml` |
| Canonical trigger | `a2d5b248-ba99-4737-af62-74e9fe6c726d`, changed from `all-pushes` to `only-build-prs` |
| Ready-for-review trigger | `99951c33-d6b9-4b9d-912e-3dda83ddf809`, `only-ready-for-review-prs` |
| Auto-cancellation | Disabled project-wide to avoid interrupting a publishing transaction |
| Main required check | Still `Python quality gate`, strict, GitHub app ID `15368` |
| Release contexts | Created and restrictions verified; credential and publisher setup remains pending |
| TestPyPI/PyPI trusted publishers | Blocked: production PyPI publisher form offers no CircleCI provider; no publication performed |
| New workflow acceptance | First PR quality matrix passed; non-main context probes denied before execution; remaining acceptance pending |
| Entitlements and activation budget | Performance / Startup verified; activation cap 2,000 credits, ten validation pipelines and one release pipeline; recurring workloads deferred |

On 2026-10-19, the production PyPI publisher form lists GitHub, GitLab, Google,
and ActiveState, but no CircleCI option. CircleCI documents the integration as
Preview. Resolve provider availability before production enablement; do not
substitute a persistent registry token. The existing Actions publisher remains
unchanged while this blocker is unresolved.

CircleCI's `only-build-prs` preset corresponds to PR opens/updates/reopens,
default-branch pushes, and tag pushes. The YAML rejects tag-only workflows.
The ready-for-review trigger covers a separate event. Verify actual event delivery
before removing Actions. Fork PRs do not trigger GitHub App pipelines: import a
reviewed contribution into a maintainer branch and open an internal PR. Do not
attach publisher contexts to contributor validation.

The API inventory found eight definitions, including a CircleCI-managed Chunk
pipeline absent from the earlier seven-item GitHub UI list. Four setup definitions
are named `deploy-markers-setup`; the deploy and rollback definitions reference
files absent from the Lite checkout. Historical setup PRs #451, #457, and #470
were merged before the Lite replacement. Existing deploy triggers remain disabled;
no definitions were deleted or recreated. Leave the Chunk pipeline untouched.

Before trigger/settings changes, the operator's local ignored
`.fleet-evidence/circleci-before/` captured definitions, trigger settings, context
names, auto-cancellation, and GitHub protection. It contains no context secret values.
The plan overview was inspected read-only; no subscription or billing settings changed.
Historical Insights spans the former application: it is not a performance baseline
for this Lite workflow. Record 10–20 representative new runs before tuning resource
classes, parallelism, schedules, or notifications.

## Account activation checklist

- [ ] Land the reviewed configuration through the existing protected delivery process.
- [x] Create `fleet-testpypi-trusted-publishing` (`dd4fdb94-6715-4399-af74-ccdea774a4cf`) and `fleet-pypi-trusted-publishing` (`6dda6797-8ec3-4213-9f3a-838370b214cd`).
- [x] Restrict all release contexts to this project, `main`, non-SSH execution and versioned config.
  Production approval additionally requires the reviewed source UUID
  `bc6f50e9-09b0-43f9-8549-55c942fd3f84`; pipeline parameters cannot override it.
  GitHub App organizations must not assume OAuth security-group features are available.
- [ ] Put a separately issued CircleCI API token under the initially selected owner `zachary@qredence.ai` in `CIRCLECI_READ_TOKEN` in the
  production publisher context. The script only uses it for workflow/job reads;
  verify the token's actual platform permissions rather than assuming its variable
  name makes it read-only. Do not use a developer's local CLI token implicitly.
- [ ] Configure separate trusted publishers on TestPyPI and PyPI with the organization,
  project, canonical definition, context UUID, repository origin, and main ref.
- [ ] Verify CircleCI's Preview OIDC support in this account using an explicitly
  authorized TestPyPI rehearsal. Do not substitute a long-lived PyPI token.
- [x] Create `fleet-github-release` (`dbec3b55-30fa-486f-a141-c4262c66b760`) with equivalent restrictions.
- [ ] Install a Qredence-owned GitHub App only on `fleet-rlm`, with Contents write and Metadata read.
  Store `GH_APP_ID`, `GH_APP_INSTALLATION_ID` and `GH_APP_PRIVATE_KEY_PEM` directly in that context.
  The finalization process uses locked PyJWT to mint a short-lived installation token, verifies
  its effective repository/permission scope, passes it only to its `gh` children, and revokes it
  on success, failure or rejected scope. No persistent `GH_TOKEN` is used.
- [ ] Confirm normal PR/main CI has no project-level provider/deployment credentials;
  audit variable names and access policy without exporting their values.
- [ ] Exercise passing/failing/updated PRs and merged-main validation, JUnit reporting,
  restricted-context denial, and production hold behavior.
- [ ] Observe the exact GitHub check identity for `quality`. Replace the old required
  check only after acceptance, preserving strictness and other protection settings.
- [ ] Remove redundant Actions quality only after the protection migration is verified.

Context creation and repository delivery are authorized by the activation plan. New
credentials, App installation, publisher trust and production approval retain their
explicit operator steps. Local validation alone does not establish any live acceptance.
If the required authorization restrictions are unavailable, leave production
publishing disabled and resolve that account limitation before proceeding.


## Non-publishing activation checks

Run the canonical pipeline with `run_activation_checks=true`, `run_release=false`.
On a feature branch, the three context probes execute only `true`; all must be
rejected as Unauthorized before execution. On `main`, they must pass, followed by
an `approve-pypi` hold and exact-user approval evidence check. The GitHub probe
mints, verifies and revokes an installation token, without creating a tag or release.
The `activation-check` helper accepts `--check approval|github` and `--receipt FILE`;
it has no upload operation. Setting both activation and release flags runs only a
failing parameter-validation job. Neither configuration replaces real release acceptance.

Activation is capped at 2,000 credits, ten validation pipelines and one continuous
0.8.0 release pipeline. Record each run ID, SHA, purpose, credit estimate and observed
usage in `.fleet-evidence/circleci-activation/`; reserve the remaining release cost
before further runs. Do not launch a second fresh release after TestPyPI consumes the
version. Recover the original immutable artifacts instead. Do not cancel a publisher
mid-transaction to save credits; stop new launches before the reserve is exhausted.

The project-variable audit found legacy `DAYTONA_API_KEY`, `DAYTONA_ORG_ID`,
`DAYTONA_TARGET` and `GITHUB_TOKEN` variables. Values were not retrieved. An operator
must retire or relocate them before declaring account-level contributor CI secret-free;
the new contexts must not inherit or repurpose those credentials.

## Requesting and approving a release

Use CircleCI's Run Pipeline dialog for the canonical definition. Select `main` and
supply `run_release=true`, `release_version=<package version>`, and
`release_sha=<full source commit SHA>`. The event must be `api`; automatic events
cannot opt into publication. The supplied commit must equal the actual checkout
and remain reachable from `origin/main`, with no tracked checkout modifications. A stale version, used registry version,
existing tag, GitHub release, or remote error stops a fresh release.

Before approving `approve-pypi`, inspect the successful quality job, original
manifest and checksums, and all three TestPyPI smoke jobs. Approval is an actual
production release authorization. To rehearse TestPyPI only, stop at the hold and
cancel the workflow; do not approve it.

The production job queries CircleCI's workflow and jobs API. It requires a successful
`approve-pypi` and the exact authorized `approved_by` user UUID, bound to the current project
and pipeline. That evidence is written before upload and retained as `approval.json`.
Context permissions remain the authorization boundary; the API evidence does not
replace them.

## Artifact identity and helper commands

The release manifest uses schema version 1 and records package/version, source SHA,
lockfile SHA-256, pipeline ID, original workflow ID, and each distribution's name,
size and SHA-256. `release/manifest.json` and `release/SHA256SUMS` are outside `dist/`.
Distributions and the manifest are handed between jobs through a workspace and
retained as build artifacts. Registry, smoke and GitHub receipts are separate job
artifacts; no status updates rewrite the original manifest.

The existing `scripts/release_checks.py` wrapper exposes:

| Command | Purpose |
| --- | --- |
| `preflight --version V --sha SHA` | Local source/version check; `--remote` additionally requires CI main lineage and unused remote identities |
| `verify-artifacts --version V --dist DIR` | Check exactly one wheel/sdist and their embedded package metadata |
| `manifest --version V --sha SHA --dist DIR --output FILE` | Create a new immutable manifest and checksums |
| `verify-manifest --manifest FILE --dist DIR` | Recheck checkout, lockfile, distribution metadata and bytes |
| `publish --registry testpypi\|pypi --manifest FILE --dist DIR --receipt FILE` | CI-only identity/approval checks, inspect existing files, upload missing originals, verify visibility |
| `registry-smoke --registry testpypi\|pypi --manifest FILE --dist DIR --requirements FILE --receipt FILE` | Download and hash exact registry distributions, install the exact wheel, exercise both CLIs |
| `smoke --version V --wheel FILE --requirements FILE` | Local isolated wheel and CLI smoke without publishing |
| `github-release --manifest FILE --dist DIR --receipt FILE` | CI-only finalization or verified recovery using the recorded source SHA |

Run commands through `uv run --locked python scripts/release_checks.py ...`.
`smoke` now requires an explicit local wheel; the former index lookup option was
removed to prevent ambiguous registry selection. Dependencies are installed from
a locked export separately, followed by the verified local wheel with `--no-deps`.
Both `fleet --help` and `fleet-rlm --help`, version readback, and API/OpenAPI imports
run without initializing the database, tracing or Daytona.

Registry verification permits at most five minutes of indexing polling, at ten-second
intervals, with bounded HTTP requests. Only a version endpoint's 404 means absent.
Authentication failures, rate limits, malformed responses and network failures stop
execution. Downloads come only from registry file hosts, use no index resolver, and
must match local manifest size and digest. A yanked or unexpected file is a conflict.

## Recovery and rollback

Never rerun a completed upload blindly and never rebuild an already-published
version. Rerun failed jobs in the original pipeline while its workspace is retained.
The manifest preserves the build workflow ID; a rerun can have a different workflow
ID. The publisher first inspects the current workflow. Only when its approval job is absent
may it recover the build workflow's exact-user approval, after rechecking project and pipeline
identity and requiring that original workflow to be failed or successful. Cancelled/running
original workflows, present-but-invalid approvals, and duplicate holds are rejected. The
receipt records both the executing and approving workflow IDs.

- If every registry file matches, upload nothing and continue verification.
- If existing files match but some are missing, upload only the missing original files.
- If a filename, digest, size or yanked status conflicts, stop and investigate.
- After an ambiguous Twine failure, inspect the remote registry on the next attempt;
  the helper does not automatically retry the upload or use `--skip-existing`.
- GitHub recovery verifies the exact tag commit and existing asset bytes before adding
  missing assets without `--clobber`. A matching tag without a release can be recovered.
  Unexpected assets, a conflicting tag, or an existing draft/prerelease stops the job;
  inspect and complete that case manually using the recorded originals. Never delete
  or move a release tag automatically.
- Missing original artifacts or trustworthy identity evidence blocks recovery. Workspace
  expiry is not permission to rebuild and reuse the published version.
- A bad PyPI package requires impact assessment, an explicitly authorized yank, and a
  corrected version. Hosted-service rollback cannot reverse an immutable PyPI upload.

Restore the previous GitHub check if cutover fails. To restore the prior trigger
policy, patch canonical trigger `a2d5b248-ba99-4737-af62-74e9fe6c726d` back to
`all-pushes` and disable the ready-for-review trigger; verify against the saved
snapshot. Re-enable project auto-cancellation only after isolating publishing from
its effects.

## Validation and deferred work

Local verification completed on 2026-10-19:

- `make check`: 794 passed, 1 skipped; lint, format, types, documentation links,
  OpenAPI and package build passed.
- Focused release/authentication regression suites: 73 passed using mocked registry/API/subprocess boundaries.
- `make check-security`: no known vulnerabilities found, with the existing advisory
  exception unchanged. The aiohttp floor was raised to 3.14.4 and the lock resolved
  3.14.5 after the previous locked version failed the audit.
- CircleCI config validation passed. An API/main event fixture compiled the complete
  release graph and confirmed the expanded supported-Python interpreter environments;
  this is static compilation, not remote job execution.
- Built the actual wheel/sdist, checked embedded metadata and manifest hashes, passed
  Twine validation, and installed the wheel in a fresh Python 3.13 environment with
  locked dependencies. Both CLIs and offline API/OpenAPI smoke checks passed.
- `git diff --check` passed.

The manifest rehearsal used the local uncommitted working tree and is validation
evidence only, not a publishable release from a clean committed checkout. Local results do not establish
remote context authorization, OIDC publication, or a measured CI improvement.

Keep scheduled audits, outbound notification integrations, higher-cost executors,
test splitting, and hosted Smart Deployments out of this delivery. For budget planning,
record executor credit rates, run frequency, median/p95 runtime and queue time, cache
transfer/install time, failures/reruns, and billing usage. Establish an explicit budget
before adding recurring or paid workloads. Keep GEPA and live evaluations separately
authorized; do not automatically rescore saved traces or install generated candidates.

See the [delivery plan](../plans/circleci-delivery-plan.md),
[CircleCI trigger guide](https://circleci.com/docs/guides/orchestrate/github-trigger-event-options/),
[PyPI OIDC guide](https://circleci.com/docs/guides/deploy/deploy-to-pypi-registry/), and
[context restrictions](https://circleci.com/docs/guides/security/contexts/).
