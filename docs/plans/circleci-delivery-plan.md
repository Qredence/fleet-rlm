# CircleCI delivery plan

Date: 2026-10-19. Status: release activation in progress; operator credential/trust steps and production approval remain gated.

Priority: PyPI and CLI releases first; hosted Fleet API deployment later.


## Implementation checklist

- [x] Inventory canonical definition, all eight API-visible definitions, context names, triggers and GitHub protection.
- [x] Preserve the canonical definition; update PR/main and ready-for-review triggers without deleting definitions.
- [x] Disable project-wide auto-cancellation to protect publishing.
- [x] Split supported-Python tests, single static/build job, dependency audit and aggregate gate.
- [x] Add pinned uv setup, interpreter-aware download caching and JUnit artifacts.
- [x] Implement source/metadata checks, immutable manifest/checksums and exact registry downloads.
- [x] Gate PyPI on TestPyPI smoke matrix, approval evidence and serialized publishing.
- [x] Implement inspect-before-retry registry/GitHub recovery and offline regression coverage.
- [x] Document operator activation, release requests, recovery and rollback in the
  [release runbook](../runbooks/circleci-releases.md).
- [x] Record final local validation and isolated artifact-smoke outcomes (794 passed / 1 skipped; audit, config and real wheel smoke passed).
- [x] Create and verify the three project/main/non-SSH/versioned-config contexts.
- [ ] Provision operator-issued credentials, GitHub App installation and registry publisher trust.
- [ ] Deliver the config and complete passing/failing/updated PR and main acceptance.
- [ ] Complete an explicitly authorized TestPyPI rehearsal and context-denial check.
- [ ] Migrate required checks and retire Actions only after remote acceptance.
- [ ] Establish a new 10–20-run baseline and an explicit operating budget.

The starting-point inventory below is historical. The runbook records the updated
settings and distinguishes implemented behavior from pending live acceptance.

## Verified starting point

- The current checkout is on `main` and was clean before adding this document.
- [CircleCI configuration](../../.circleci/config.yml) runs `make check` on Python 3.11, 3.12,
  and 3.13, audits locked dependencies, and aggregates success into a `quality` job.
- A manual parameter enables release preflight, one wheel/sdist build, TestPyPI publishing,
  an isolated package smoke test, PyPI publishing, and a GitHub release. There is no approval job
  between the TestPyPI smoke test and production publishing.
- Python 3.14 is a manual informational probe, consistent with the current supported range.
- GitHub Actions quality still exists. CircleCI has no dependency cache or JUnit upload steps.
- The CircleCI UI shows Qredence / `rlm`, project UUID `122b2cc9-5a9c-4b3b-88c6-9276bd5d5d98`,
  with GitHub App config and checkout sources pointing to `fleet-rlm`.
- Project Setup shows seven definitions: `build-and-test`, `deploy-pipeline`, `rollback-pipeline`,
  and four `deploy-markers-setup` definitions. `build-and-test` has an enabled all-pushes trigger;
  the deploy definition has a disabled all-pushes trigger. Other setup definitions have absent
  or disabled triggers. Their existence does not prove successful execution.
- Deploy and rollback definitions reference `.circleci/deploy.yml` and `.circleci/rollback.yml`;
  neither file exists in this checkout. Inspect remote branches and generated PRs before deciding
  whether these are unfinished setup or obsolete definitions.
- Account entitlements, credit balance, successful runs, branch protection, contexts, and PyPI
  publisher configuration have not been verified. There is no measured CI performance baseline.

## Phase 1: Establish one reliable CI entry point

1. Inventory definitions, triggers, generated PRs, recent runs, and current required checks.
   Retain `build-and-test` as the proposed canonical definition; record its ID before configuring OIDC.
   Resolve duplicate setup definitions only after their use is understood and cleanup is authorized.
2. Configure GitHub App events for PR creation, updates, ready-for-review, and pushes to `main`.
   Align UI trigger events with YAML conditions and avoid duplicate push/PR work.
   The present all-pushes trigger alone is not evidence that PR-event conditions execute.
3. Preserve the Python 3.11–3.13 matrix, dependency audit, and aggregate `quality` gate.
   Keep 3.14 optional. Test ordinary PRs, failing PRs, updates, and mainline merges.
   Document a separate policy for fork contributions: GitHub App PR triggers have fork limitations.
4. Keep Actions until CircleCI passes the acceptance cases, then explicitly migrate required
   checks using the observed GitHub check identity and retire redundant Actions quality execution.

Acceptance: one intended workflow per event; a failing test blocks the aggregate gate;
PR and `main` coverage work; no release jobs run on ordinary pushes or PRs.

## Phase 2: Make package releases deliberate and recoverable

Target flow:

```text
Explicit release request on main, with version and full source SHA
  -> supported-Python quality and dependency audit
  -> source/version/tag/index preflight
  -> build wheel and sdist once; record hashes
  -> publish exact artifacts to TestPyPI
  -> download and verify hashes; isolated CLI/package smoke on Python 3.11–3.13
  -> manual production approval
  -> recheck source identity and artifact hashes
  -> publish the same artifacts to PyPI
  -> verify PyPI downloads and CLI entry points
  -> GitHub release/tag targeting the recorded SHA; attach artifacts and checksums
```

- Extend the existing release checks rather than build another release framework. Preflight must
  detect already-used versions and existing tags before uploads; index failures must stop the run.
  Make TestPyPI smoke installation select the exact TestPyPI artifact, with a bounded indexing wait,
  rather than allow a same-version package from another index to satisfy the check.
- Configure separate TestPyPI and PyPI trusted publishers, bound to the canonical organization,
  project, pipeline definition, context IDs, and repository/ref where available. CircleCI documents
  this OIDC integration as Preview: verify account availability with TestPyPI before production use.
- Attach publishing contexts only to publishing jobs. Apply project and main-branch restrictions
  and prohibit SSH reruns; verify which approver restrictions the actual organization supports.
  A pipeline boolean and an approval button alone are insufficient publishing authorization.
- Keep the GitHub release credential in a separate restricted context with minimum permissions.
  Retain the existing Twine 7 compatibility range; use pinned uv/tooling consistently.
- Store version, commit, pipeline/workflow IDs, artifact hashes, approver, and registry links in
  a release manifest. Use workspaces within a workflow and durable release artifacts for recovery.
- Serialize production publishes and reject overlapping requests for the same version.
  Before resuming a failed publish, inspect registry files and hashes. Resume missing steps only;
  never blindly rebuild or repeat an upload that may already have succeeded.

Acceptance: unauthorized refs cannot publish; TestPyPI proves OIDC and exact-artifact installation;
approval gates PyPI; all published hashes match; a simulated partial release has a documented recovery.
The first production publish remains an explicitly authorized action.

Package incident response: stop promotion, inspect impact, yank a bad release when authorized,
and publish a corrected version. Users may pin a previous good version. A PyPI upload is not a
hosted deployment that CircleCI can automatically reverse.

## Phase 3: Add useful feedback and control credit use

| Capability | Fleet application | Validation |
| --- | --- | --- |
| Dependency caching | Cache uv downloads by cache version, OS/architecture, Python/tool version, and `uv.lock`; keep locked sync | Compare cold/warm install time and credit use |
| Test reports | Upload pytest JUnit results and failure artifacts | Tests tab identifies failing tests and durations |
| Insights | Track median/p95 duration, queue time, success, reruns, and credits | Baseline across the first 10–20 representative runs |
| Less duplicate work | Run lint/type/docs/OpenAPI/build once; preserve tests on all supported Pythons | Same coverage and gate semantics with lower measured cost |
| Timing-based splitting | Add only if pytest dominates after caching | Lower latency with acceptable credit increase and no missing tests |
| Schedules | Weekly locked-dependency audit and link checks; optional compatibility probes | Schedule selects maintenance only, with no publish/live jobs |
| Notifications | Failed mainline runs, approval needed, release success/failure | One actionable event with commit and run links |
| Reusable commands | Share pinned uv setup and locked installation | Smaller config with unchanged execution behavior |

Set the monthly budget after checking the account plan and observed costs. Estimate compute as
sum of job minutes times executor credit rates, multiplied by expected pipeline volume; add storage
and any feature charges. Use billing usage to reconcile Insights, which is not a precise bill.
Set job timeouts and retention limits; cancel superseded validation runs where supported without
interrupting publishing. Defer Docker layer caching, larger executors, custom orbs, self-hosted
runners, and dynamic config until measurements show a need. Do not retry deterministic failures.

Acceptance: baseline and agreed credit budget exist; performance changes improve measured cost
or latency without weakening checks; schedules and notifications do not create duplicate work.

## Phase 4: Hosted API and Smart Deployments

Start only when a hosting target, authenticated ingress, staging/production resources, artifact
registry, monitoring source, and operating budget have been chosen. The current API has no caller
authentication; a non-loopback bind flag does not provide an authenticated deployment boundary.

1. Build an immutable deployable artifact from the approved source and identify it by digest.
   Promote that same digest through staging and production; serialize deploys per environment.
2. Introduce deploy markers and an environment/component record. Implement deploy and rollback
   pipelines that target a recorded artifact, with restricted contexts and production approval.
3. Validate `/health`, expected package version and MLflow destination, plus separate database,
   SSE/session lifecycle, and cleanup checks. `/health` alone does not prove all dependencies work.
   Live model/Daytona checks need separate authorization, disposable resources, and spend limits.
4. Rehearse rollback in staging. Drain active Turns, preserve session leases and durable state,
   and check old-code/new-schema compatibility. Current startup applies additive migrations;
   service rollback must not imply automatic destructive database reversal.
5. Connect Smart Deployments validation to release-specific monitoring signals. Start with
   reporting/manual recovery, then enable automatic rollback only after a successful drill.
   Define the observation window, missing-signal behavior, thresholds, and rollback failure response.
   Provider-wide model/Daytona incidents need different treatment from a regression in this release.

Acceptance: staging promotion, production approval, and rollback drills succeed; failures are
visible; missing or unrelated monitoring signals cannot silently justify a healthy release.
Smart Deployments does not require Kubernetes or a release agent. Consider progressive rollouts
only if the eventual host and traffic volume justify them.

## Evaluation boundary and implementation order

Keep normal CI offline. MLflow traces may inform separately approved canaries; GEPA search and
program installation retain their existing evidence and approval mechanisms. CircleCI must not
automatically rescore saved traces, run paid optimization, or install a generated candidate.

Implement as separate reviewable changes: (1) triggers/gate/reporting, (2) guarded package release
and recovery, (3) measured caching/maintenance, (4) hosted deployment and Smart Deployments.
Run focused checks and the full required quality gate for code/config changes. Preserve the last
working required check until its replacement is verified. Stop on source/hash mismatch, failed
tests, unauthorized context, ambiguous publication, absent approval, or exceeded agreed budgets.

## References

- [GitHub trigger event options](https://circleci.com/docs/guides/orchestrate/github-trigger-event-options/)
- [Contexts and restrictions](https://circleci.com/docs/guides/security/contexts/)
- [CircleCI PyPI OIDC publishing, Preview](https://circleci.com/docs/guides/deploy/deploy-to-pypi-registry/)
- [PyPI trusted publishing security](https://docs.pypi.org/trusted-publishers/security-model/)
- [Persistence: caches, workspaces, artifacts](https://circleci.com/docs/guides/optimize/persist-data/)
- [CircleCI Insights](https://circleci.com/docs/guides/insights/insights/)
- [Schedule triggers](https://circleci.com/docs/guides/orchestrate/schedule-triggers/)
- [Smart Deployments](https://circleci.com/docs/guides/deploy/smart-deployments-overview/)
- [Rollback pipelines](https://circleci.com/docs/guides/deploy/set-up-rollbacks/)
