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

Use the same request with `parameters: {"run_python314": true}` for the probe.
The explicit pipeline ID avoids dispatching an agent/deploy definition accidentally.
This is the current [V3 run API](https://circleci.com/docs/api/v3/#runs);
older CLI examples using `circleci pipeline run` / `--param` are obsolete.

The Python 3.14 probe is informational and does not change the supported
Python range (`>=3.11,<3.14`).

## SonarQube Cloud

The `sonarqube` job analyzes `Qredence_fleet-rlm` in the `qredence` organization
on SonarQube Cloud EU. It uses the official SonarScanner CLI 8.1.0.6389 Linux
x64 distribution, verifies its pinned SHA-256, and waits up to five minutes for
the server quality gate. Its full Git checkout includes the historical blobs
needed for SonarQube source blame. The aggregate `quality` job, and therefore release
preflight, requires this job to pass.

Python 3.11 runs the full suite across two executors. Each executor preserves its
selected test-file manifest and parallel Coverage.py data, including when tests
fail. A report job waits for both executors to reach a terminal state, verifies
that their manifests cover every discovered test file, combines the data, writes
`coverage/coverage.xml`, and then enforces the 80% floor. Its teardown uploads the
report directory after XML generation, so a floor failure retains the XML
diagnostic. Successful reports share only the XML through the workflow workspace.
Coverage.py uses relative file paths so the scanner can resolve the report in its
own checkout. The Python 3.12 selective suite and Python 3.13 full suite retain
their separate roles; SonarQube imports the combined Python 3.11 coverage only.
Runtime and repository scripts are analyzed, while coverage measures the shipped
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

## Smarter Testing

Python 3.12 uses sorted relative test-file atoms and CircleCI's documented
per-atom LCOV analysis. Each analysis run executes one selected file with Coverage.py,
then writes `<< outputs.lcov >>` using `coverage lcov --fail-under=0`. The LCOV
contract test verifies relative source paths and executable-line records. This
avoids single-pass-only dependencies while retaining context that each file atom
covers its source. Python 3.11 and 3.13 each run all tests split over two
executors, with test durations reported for the 30 slowest tests. Python 3.11
collects parallel coverage across both shards; only their combined report
enforces the 80% floor. Python 3.13 remains an independent full-suite interpreter
check. These jobs use fixed parallelism of two; Python 3.12 continues its existing
one or two-worker impact selection and per-atom LCOV analysis.

Compatibility audit on 2026-11-01 used CircleCI CLI core 1.13.0 with an isolated
testsuite extension install at 1.0.68450-8be2f5e, pytest 9.1.1, coverage.py 7.16.2,
and `pytest-circleci-coverage` 0.3.883. The minimal one-file format/parser
reproduction passed, including the file-to-test mapping. Fleet doctor then failed
its sampled run because the sandbox fixture was not loaded in that invocation.
That did not meet the required doctor acceptance, so the one-attempt rule selects
the LCOV fallback. The explicit `-c pyproject.toml` run option fixes fixture loading
for Fleet's doctor. Both single-pass packages have been removed; the user's
installed CLI was not replaced.

Final Fleet doctor with the isolated extension discovered 66 atoms, passed all 12
sampled run atoms, and parsed all 12 sampled LCOV mappings with no action items.

Impact key `fleet-ci-files-lcov-v1` isolates the LCOV/file-atom map. The first
authorized `main` run seeds it. Missing impact data must select all tests.
Changes to dependencies, tests/fixtures, CI/build configuration, repository scripts,
OpenAPI, bundled Skills, and sandbox requirements force a full run. When adding
non-Python runtime inputs, add them to `full-test-run-paths` too.

Dynamic splitting balances the selected files over one or two workers. Both the
upstream `list-tests` and downstream `run` commands use default selection flags;
change both together if adding overrides. One automatic retry is allowed. Review
pass-on-retry evidence as a defect; do not increase retries to mask failures.
JUnit includes file attributes; explicit `store_test_results` steps own uploads.
Teardown retains available reports on cancellation/timeouts. Python 3.11 exports
XML before enforcing coverage, so threshold failures retain diagnostics; only
successful report jobs publish the workspace consumed by SonarQube. The Python
3.11 test job records each executor's exit status and persists coverage data
before returning that status, allowing the report job to export whatever data
was collected even when a shard's tests fail. The separate test job remains a
required success gate.

### Local test latency and MLflow warning investigation

The full local backend suite remains available on demand but no longer blocks
every push. Run it explicitly with:

```bash
uv run pre-commit run backend-tests --hook-stage manual --all-files
```

`make test` remains available for direct invocation. CI's pytest output includes
the 30 slowest tests. When investigating MLflow lock warnings, compare those
durations with the warning timestamps and CircleCI test logs across the 3.11 and
3.13 shards. The autouse fixture continues to point tests at the session-scoped
temporary SQLite store; do not change tracing or database behavior unless the
timing and warning evidence identifies a specific contention source.

Offline diagnostics (doctor executes sampled tests and per-file coverage, but must
not invoke live Fleet services):

```bash
uv sync --locked --dev
make check-ci-config
circleci testsuite doctor "ci tests"
circleci testsuite run "ci tests" --local --run-tests=all --analyze-tests=none
```

Doctor and local impact/state files and test reports are ignored. To rebuild a
local map, use `--local --run-tests=none --analyze-tests=all`. To reset incompatible
cloud data, bump `impact-key` in a reviewed change and seed it on `main`; do not
reuse data from another atom naming scheme.

Paid Smarter Testing charges count discovered atoms, even when selection runs only
a subset. Recount with the discovery command and check current plan usage before
forecasting; the historical 10,000-atom allowance is not an enforced spending cap.
Keep the worker maximum at two. Do not increase workers or spending without an
explicit decision. See [Smarter Testing](https://circleci.com/docs/guides/test/smarter-testing-overview/).

## Release notes and package publication

The release sequence is:

`quality → preflight → build once → TestPyPI → PyPI → canary + validation → GitHub`

Before running it, verify the requested version is unpublished and choose its exact
full `main` source SHA. The build captures that SHA's unique nonempty changelog
section, locked consumer requirements, and their SHA-256 identities in
`release-metadata/`, bound to `dist/SHA256SUMS`. These are CircleCI artifacts and
workflow inputs, not files to commit. Missing/empty/duplicate notes fail before
any publication. Uploads reuse the same verified distribution bytes.

In the explicit V3 run request above, set `parameters` to:

```json
{"run_release": true, "release_version": "<version>", "release_sha": "<full-sha>"}
```

TestPyPI rehearsal is enabled by default. If its OIDC exchange reports an unsupported
issuer, first confirm no candidate was uploaded, then obtain explicit approval for
`"skip_testpypi": true` in the parameters. This retains the PyPI checksum, isolated canary,
validation window, and GitHub gates. The PyPI upload uses the restricted
`fleet-pypi-upload-token` context; TestPyPI uses `fleet-testpypi-trusted-publishing`.
Never put publishing credentials in project-wide variables or task prompts.

GitHub publication uses captured notes, not notes regenerated from the current
checkout or an agent at publication time. It verifies the source, metadata, manifest,
and successful canary receipt from the same workflow, then publishes distributions
and `SHA256SUMS`. Normal and recovery runs share the same publishing job. A repeat
is a no-op only for an identical existing tag target, notes, and downloaded asset
checksums. A conflicting or partial release fails for manual inspection; assets are
never overwritten. GitHub publication uses only `fleet-github-release`.

## PyPI Smart Deployments

Verified read-only on 2026-11-01: component `fleet-rlm` and environment `pypi` exist.
Version 0.9.0's canary and release-validation jobs passed; GitHub publication waited
for both. This proves the historical success path, not failure-webhook delivery.

The normal plan is `fleet-rlm-pypi`; recovery uses `fleet-rlm-pypi-recovery`.
Marker/webhook identities live in `scripts/release_checks.py`. Both typed release
jobs share one validation policy: enabled, 20-minute window, custom `pypi_canary`
check, `criteria == "triggered"`, one failure, and automatic rollback disabled.
The payload explicitly includes component, environment, signal, version, and project
UUID; mappings must match those fields. The webhook secret is in the restricted
`fleet-pypi-validation-webhook` context. Its metadata and project/main/non-SSH/non-API
restrictions were inspected without reading the credential. TestPyPI, PyPI upload,
and GitHub release context restrictions were likewise checked.

Uploader tools are prepared before the pending plan starts. Upload is bounded to ten
minutes, below CircleCI's pending-marker timeout, and `RUNNING` is recorded only
after upload succeeds. The canary has a fifteen-minute outer deadline that includes
uv bootstrap and environment installation. uv's installer is pinned and verified
against its GitHub release asset SHA-256 in `.circleci/install-uv.sh`.

The canary checks public PyPI wheel and sdist metadata against the manifest,
downloads only from public PyPI without alternate indexes/cache, verifies the wheel's
bytes, installs captured source-version runtime requirements, and smoke-tests the
CLI and API in an isolated environment. It records source/artifact identity, checks,
workflow/project/plan identifiers, and outcome in `canary-results/receipt.json`.
A failed rerun clears previous success evidence before validation. GitHub requires
both the successful canary job/receipt and the typed release-validation job.

Failure jobs send the named failure signal and explicitly mark deployment health
failed, including setup failures and timeouts. Separate canceled-job dependencies
record `CANCELED`; cancellation must not run a second failure finalizer in teardown.
Teardown preserves available diagnostic artifacts. Full workflow cancellation may
also stop cleanup jobs: inspect the Deploys record and reconcile it manually.
A rejected terminal marker update must be inspected; do not force a completed
release into a new state. Rerun the full recovery workflow instead.

### Integration acceptance, without uploading a package

Use the prepared [.circleci/validation-acceptance.yml](../../.circleci/validation-acceptance.yml)
and `scripts/validation_acceptance.py` fixture only after an operator authorizes an
isolated integration run. It plans component `fleet-rlm-validation-acceptance` in
environment `pypi-validation-acceptance`, copies the production 20-minute named-check
policy with rollback disabled, and contains no package upload or GitHub publication
job. A temporary CircleCI pipeline definition must point at this config and retain
the project/main restrictions. The `fleet-pypi-validation-webhook` context must be
available to the failure scenario under that definition's approved restriction.

Run one scenario parameter at a time: `success` proves a fixture-only receipt and
lets the validation window finish; `failure` starts validation, then pauses at
`wait-until-listening`. Approve that job only after the UI shows **Listening**. The
next job sends the matching isolated failure payload; verify that the event attaches
to `pypi_canary`, validation fails, and `downstream-sentinel` is skipped. For
`cancellation`, cancel only `acceptance-cancellable`; verify the dependent cleanup
job records `CANCELED`. The fixture receipt is not package-health evidence; the real
workflow's public-PyPI canary remains authoritative. Preserve workflow/deployment
IDs, panel evidence, payload metadata without headers, and terminal outcomes. Do not
create the temporary pipeline definition or run any scenario as part of local checks.

HTTP 2xx only means webhook acceptance, not attachment to a listening check.
CircleCI documents no supported API to await Listening. Validation can pass when its
window expires without any signal, so a green validation job alone is not proof of
package health. The direct canary/receipt gate remains mandatory even if a failure
event races window startup. No sleeps or undocumented readiness API are used.
See [manual validation](https://circleci.com/docs/guides/deploy/set-up-release-validation-manually/)
and the [routing reference](https://circleci.com/docs/guides/deploy/release-validation-reference/).

On the next authorized real release, confirm the exact version/digests, receipt,
Listening window, attached failure events when applicable, and GitHub gate ordering.
No controlled cloud verification or new package upload is implied by offline tests.

### Failure and recovery

PyPI files cannot be overwritten and this pipeline has no automatic registry rollback.
After a defective publication, inspect/yank it manually and publish a corrected
version. Do not re-upload the same version or silently promote validation. If the
package is correct and only CI infrastructure failed, use no-upload recovery:

In the explicit V3 run request above, set `parameters` to:

```json
{"run_pypi_recovery": true, "release_version": "<published-version>", "release_sha": "<published-source-sha>"}
```

Recovery requires the published source to be an ancestor of current `main`, rebuilds
that exact source, captures its historical notes and requirements with the current
extractor, verifies published digests, and opens a separate validation record. It
never uploads to a registry. A checksum mismatch blocks recovery; inspect rather
than rerunning or replacing evidence. Preserve completed artifacts and run IDs.

## Chunk tasks and AI delivery feedback

[CircleCI's AI SDLC guidance](https://circleci.com/blog/ai-sdlc/) informs a bounded
loop: named intent/failure → evidence → small fix/draft → offline checks → reviewed
PR → validated release → receipt/outcome fed back to a future repair. Agents propose
and validate; humans retain architecture, merge, and release decisions.

`.chunk/run.json` dispatches `dev` to `chunk-task-pipeline`
(`8b3162e2-16da-44ad-9e09-3f44d5777838`), not the ordinary build pipeline. The
existing `build-and-test` pipeline remains the task validation target. The agent
setup has exactly one `cci-agent-setup` job/workflow, installs locked Python 3.12
dependencies, exports/verifies the interpreter, and attaches no publishing contexts.
`chunk validate` uses Make targets for static checks, coverage, build, and both CI
configuration validations. Remote sidecar/cloud tasks consume credits and model
tokens and require an operator request. Keep the existing provider/spending controls.

### Credential migration and Chunk activation gate

The 2026-11-01 metadata-only audit returned these project-wide variable names:
`GH_TOKEN`, `GITHUB_TOKEN`, `DAYTONA_API_KEY`, `DAYTONA_ORG_ID`, and
`DAYTONA_TARGET`. No value was requested or printed. The CircleCI CLI reports
project-variable values only as masked placeholders; they cannot be exported
after creation. The existing `fleet-github-release` context contains `GH_TOKEN`
and is restricted to this project, main, and non-SSH/non-API configuration. The
current release jobs already use that context. Older `github-release` and
`release-automation` contexts contain `GITHUB_TOKEN`; audit their consumers before
removing them.

| Project variable | Known consumer and required access | Prepared destination | Verification before removal | Rollback |
| --- | --- | --- | --- | --- |
| `GH_TOKEN` | Current normal/recovery GitHub release jobs need repository release/tag/asset write access. | Keep the existing `fleet-github-release` context; no project-level copy is needed after cutover. | Run a controlled publication check in a sandbox repository with the same restricted context, then verify the `build-and-test` release job receives context access only. | Reissue from the GitHub credential source of truth and recreate the project variable only if the restricted context fails. |
| `GITHUB_TOKEN` | No use is present in the checked-in primary config or `.chunk` files. It may be consumed by older remote pipeline definitions or Chunk setup. | Chunk PR creation should use its managed GitHub App installation. Any confirmed legacy job gets a separately named least-privilege context; routine CI gets no token. | Create a draft PR in a sandbox repository with the managed App and each confirmed legacy consumer; verify non-PR jobs cannot see a token. | Restore a replacement from the GitHub source of truth or reauthorize the App; never recover a value from logs or a running job. |
| `DAYTONA_API_KEY` | No reference exists in checked-in CircleCI or Chunk config. Likely consumers are live Daytona-backed agent/runtime tasks; exact job/task ownership is unverified. Requires only the Daytona project/org permissions those consumers use. | Prepare a `fleet-daytona-live` restricted context only after the remote consumers and minimum scope are confirmed. Add it to named live-service jobs, never normal tests or release jobs. | Use a dedicated non-production Daytona target for an authorized auth/connectivity smoke check, then audit which jobs accessed the context. | Reissue/rotate in Daytona's source of truth, verify the replacement, then restore the old project value only as a temporary emergency rollback. |
| `DAYTONA_ORG_ID` | Same unresolved consumer set as `DAYTONA_API_KEY`; organization routing metadata is not itself sufficient authorization. | Same `fleet-daytona-live` context, only where required with the API key. | Confirm the selected organization and target are the approved non-production pair without logging the key. | Restore from the Daytona source of truth; do not recover from CI output. |
| `DAYTONA_TARGET` | Same unresolved consumer set; selects the Daytona execution target. | Same `fleet-daytona-live` context, limited to live-service jobs. | Verify that the designated job uses the intended isolated target and that ordinary CI has no Daytona context. | Restore the previously approved target identifier from its source of truth. |

The pipeline-definition inventory is also incomplete. The API lists `build-and-test`
(`fe82e142-4105-421e-a525-eb47298d5e7a`, checked-in `.circleci/config.yml`),
`chunk-task-pipeline` (`8b3162e2-16da-44ad-9e09-3f44d5777838`, configured from
`circleci-agents/configs/chunk/config.yml`, not present in this checkout),
`deploy-pipeline` (`3078fcb4-7056-4c90-8ecc-7f2831046a53`, `.circleci/deploy.yml`),
`rollback-pipeline` (`330a2c33-9a78-4f30-8dad-ecd897ab3465`,
`.circleci/rollback.yml`), and four historical `deploy-markers-setup` definitions
(`47909734-57e6-4f6b-8b02-fb867c1c6fea`, `cdd26158-a2e2-49f0-ac8f-442c8184c726`,
`0f2376c8-8b4c-4bd8-a583-c7cca7ba257c`, and
`3ce81903-5623-47a1-8a2e-3b4bfbe53ce4`; all point at `.circleci/config.yml`).
The deploy/rollback and Chunk source files are not in this workspace, so their
variable consumers cannot be cleared from this audit. Obtain and review the exact
config revisions and recent job context-access records before cutover. Retain each
definition until its trigger/run owner confirms it is obsolete.

**Cutover is a separate approved operation.** Before requesting it, the operator
must map every variable to the exact job/task and source-of-truth owner, prepare
replacement values where needed, create restricted contexts, verify replacement
access for all required consumers, and record rollback owners. Only then remove the
project-wide copies through CircleCI project settings. Verify the variable names
are absent, release-context jobs retain their restricted access, ordinary CI has
none of these credentials, and Chunk uses managed App access for draft PRs. If a
value is unavailable from its source of truth, issue a replacement; do not extract
it from logs, artifacts, or live jobs. Do not activate a cloud agent task until
this cutover is complete. Keep provider selection and spending controls unchanged.

Versioned prompts and the shared checklist are maintained in:

- [CI repair](../../.circleci/tasks/ci-repair.md): requires the original failed run ID.
- [Release preparation](../../.circleci/tasks/release-preparation.md): requires target
  version and previous tag; produces reviewed notes/version changes, never a release.
- [Review checklist](../../.circleci/tasks/review-checklist.md): architecture, regression
  evidence, permissions, release provenance, and honest uncertainty.

After the activation prerequisite and explicit task authorization:

```bash
chunk task run --definition dev --branch main --new-branch \
  --prompt "$(cat .circleci/tasks/ci-repair.md) Target run UUID: <run-id>"

chunk task run --definition dev --branch main --new-branch \
  --prompt "$(cat .circleci/tasks/release-preparation.md) Target version: <version>; previous tag: <tag>"
```

The task must use a new branch and draft PR, preserve failing-test evidence, run
focused regression checks then `make check-ci-config` and `make check`, and report
run links and uncertainty. No automatic merge, publishing, validation promotion,
weakened gates, or live Fleet evaluation. A green retry feeds a future flaky-test
investigation rather than being treated as a harmless race. Read the shared checklist
as part of both tasks; do not synchronize developer prompts into Fleet user Skills.

Native weekly flaky-test configuration is prepared as: one test, one candidate,
five validation runs, maximum one open PR. Do not activate on this organization:
current [native-task documentation](https://circleci.com/docs/guides/test/fix-flaky-tests/)
requires a GitHub OAuth organization, while this project uses `circleci/<ID>`.
Recheck support before activation; until then use on-demand repair. No custom
scheduler or organization migration is included.

## Triggers, measurements, and rollback

On 2026-11-01, redundant-workflow auto-cancel was already enabled. Keep it enabled.
The combined `only-build-prs` trigger (`a2d5b248-ba99-4737-af62-74e9fe6c726d`) stays
active. The overlapping ready-for-review trigger (`99951c33-d6b9-4b9d-912e-3dda83ddf809`)
was disabled reversibly. After an authorized push, verify a draft PR open/update and
main push each produce the intended pipeline once. Branch protection must continue
requiring the observed aggregate `quality` status. Leave obsolete pipeline definitions
alone until their consumers have been audited.

The successful 2026-10-31 workflow `a96f94c8-ffea-4b6d-b093-8085c4a3a97d`
spent 12m38s on Python 3.12 (two workers), versus 6m14s on Python 3.11 and 4m56s
on Python 3.13. Cache restore was 4–8s and dependency installation 4–5s, so repeated
test/analysis execution is the first target. This is one observation, not a forecast.

Record at least three comparable successful PR runs: total required-workflow and
per-job duration, executor consumption, cold/warm cache state, selected atoms,
coverage shard balance, and retries. Target a warm-cache median required-workflow
duration of five minutes or less while preserving every test and gate. Compare
executor consumption separately because splitting 3.11 and 3.13 increases their
parallel executor count. If the target misses, report the measured bottleneck and
revise the plan before adding more parallelism. A temporary source-change probe
must still show its known regression test selected; config/fixture changes must
run all 3.12 atoms.

If the LCOV optimization fails acceptance, restore full test execution while
retaining the correctness, reporting, interpreter, and release-provenance fixes.
Keep the two-worker cap. Cache
restructuring, larger executors, and further paid-feature expansion require evidence.
To undo trigger deduplication, re-enable the saved trigger ID with
`circleci project trigger update <trigger-id> --enable`.
