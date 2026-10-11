# PRD 2 Daytona lifecycle acceptance receipt

Status: implemented and verified in the working tree. All seven PRD 2 acceptance criteria have evidence. This receipt covers
PRD 2 and bounded verification contributions to PRDs 3, 8, 9 and 12. It does not
complete those other PRDs.

## Baseline and scope

Baseline commit: `3cb6bf15e`. Existing edits to `README.md` and the untracked
`fleet-rlm-improvement-plan.md` were preserved. No dependency, schema, public API,
stream, configuration policy or observation retention change was made.

Existing concrete operations moved to `provider.py`, ownership/receipts to
`leases.py`, child execution to `children.py`, and serialized host I/O to
`host_io.py`. `DaytonaRuntime` still coordinates roots, binding generations,
revocation and shutdown. No new protocol, provider adapter or compatibility
facade was added. Duplicate late-task and active host-lease bookkeeping was
removed. Child deletion uses one concrete request/probe path; shutdown and
confirmation errors are never retried by guessing call signatures.

The ownership transition table and authoritative records are documented in
[Daytona lifecycle](../reference/daytona-lifecycle.md). Process-local counts
are derived observations; only provider probes certify absence.

## Authorized live scope

The operator explicitly granted full permission to use the configured existing
environment. No infrastructure was provisioned and no migrations ran.

| Setting | Value |
| --- | --- |
| Python | 3.13.13 |
| Daytona SDK | 0.220.0 |
| DSPy | 3.4.0 |
| MLflow | 3.16.1 |
| Database | Existing PostgreSQL `fleet_rlm`, schema head `01a087800003` |
| Daytona Volume | `fleet-volume`, ID `e86bb2ec-c0c6-49d8-ba47-9eeebb4452d0` |
| Root/host snapshot | `fleet-rlm-python313-v10` |
| Child snapshot | `fleet-rlm-python313-child-v5` |
| Root/child model | `uscentral.ai_gateway.deepseek-v4-1-flash-service` |
| MLflow destination | Existing local server, port 5001, experiment `fleet-rlm` |
| Validation Session | `ab9ac10c-7036-47fb-857e-edd7fd6a83c7` |

Actual product command: `uv run fleet cli --port 8012`, in a 200×50 tmux
terminal. `/trace` supplied the full IDs; `MlflowClient.get_trace` retrieved
complete traces. SQL Run state/replay and SDK `get`/`list` supplied independent
evidence. Local trace/probe artifacts are in the ignored
`.fleet_rlm/prd2-validation/` directory. This committed receipt contains identifiers and validation outcomes only.

## Acceptance mapping

| Criterion | Evidence |
| --- | --- |
| AC-02-01 late acquisition ownership | `test_runtime.py`, `test_child_lease_cleanup_ownership.py` and `test_sandbox_lifecycle.py`: late creation/adoption, cancelled acquisition, retained provider tasks and permit release. |
| AC-02-02 non-cancellable work | `test_session_manager.py`, `test_turn_coordinator_cancellation.py`, host-I/O and child ownership suites: shielded drain and retained ownership. Real remote Python cancellation is separately recorded below. |
| AC-02-03 failed deletion | Existing request-acceptance, quarantine, absent, repeated-close and exactly-once release tests. New parametrized child confirmation regression verifies a body `TypeError` is called once and preserves its permit until a later confirmed retry. |
| AC-02-04 fresh warm bindings | Cold/warm CLI checks below plus adapter/root binding regressions; Python globals are refreshed while Volume data and SQL history persist. |
| AC-02-05 lifecycle matrix | Deterministic runtime/session suites cover cold, warm, stale, failed, replacement and concurrent acquisition, generations and autostop. |
| AC-02-06 live semantics | Exact SDK/snapshots recorded above; CLI, complete trace inspection and independent provider confirmation below. |
| AC-02-07 fence ordering | Session manager and coordinator cancellation tests cover root/associated-child fencing before blocked drain and retained failed fences. |

Cross-PRD coverage retains mount and child isolation, broker claim uniqueness,
sanitary diagnostics, committed-history replay, and observation outage/no-export
behavior. Existing MLflow runtime, export-outage and public tracing-disabled
suites verify telemetry remains outside commitment/cleanup authority.

## Live evidence

| Scenario | Run / trace | Result |
| --- | --- | --- |
| Cold Python | `39154ce9-9abd-48ad-ad44-49ded1aae612` / `tr-d2b3fa957839b1939523e6cee2ee212b` | Actual Python wrote 31 bytes, read them back and computed sum 10. Commit revision 1, claim released. |
| Warm Python | `3f4a36eb-e478-4981-b556-2d1d29b1a1fe` / `tr-e96810c33b4ab34004d49ca2d72ff87e` | Same root ID `31f28177-e8c0-43b3-9446-4695f0407bb2`, generation 1. File unchanged; previous `payload` variable absent. Commit revision 2, claim released. |
| Cancellation | `1ad29032-489f-4690-9768-f2c6b4eac8d7` / `tr-57076522c77348d315379d3d97417c0f` | Remote marker read before Esc. SQL status cancelled, no commit revision, claim released, binding quarantined. Independent SDK probe: stopped. Trace has completed cleanup and no `database.commit`. |
| Root replacement / resume | `9a20cb01-a5de-4794-80c4-64e824ad762d` / `tr-baba44a7b620e7c75655411b074fecac` | New root `fc0eb961-97f4-43c0-9668-61e79e66954b`, generation 2, same Volume. Exact checksum preserved; start marker persisted and late-write sentinel absent after original sleep window. SQL history tool reloads cold/warm/child answers. |
| Child investigation | `bcaf1300-5c00-4288-afa2-919106525baa` / `tr-5cbf6c8e1c2b948f2a4aa4cdd0ed941a` | One actual recursive child; Python marker and sum 66. Complete acquire/stage/invoke/harvest/cleanup phases; cleanup reports confirmed. Independent provider confirmation is supplied by the additional identified child below. |

File: `/workspace/prd2-lifecycle-proof.txt`; exact bytes
`prd2-lifecycle-proof-2026-10-10`. SHA256:
`f2a9b0dd0fd0420ee98763d94b1c8d1f2d0b80ca217914a904d077ed85e08752`.
Cold and warm traces each contain three `sandbox.execute` spans, one successful
`database.commit`, settlement and cleanup. The child trace contains five
`sandbox.execute` spans and two RLM execution branches. Trace success alone is
not deletion confirmation.

## Validation commands

- Baseline focused six suites: 297 passed.
- After concrete extraction, focused ownership suites: 296 passed (one redundant
  signature-probing test removed).
- Broader role/coordinator suites: 268 passed.
- Adversarial/root/session verification: 294 passed.
- Updated composition tests: 38 passed.
- Latest sandbox lifecycle suite: 100 passed.
- `TMPDIR=/tmp/fleet-prd2-tmp make check`: exit 0; lint, formatting, types, live-test collection, backend coverage 83.75%, TUI 31 files / 474 tests, architecture and documentation checks passed.
- `make check-docs`: exit 0. `git diff --check`: exit 0.

Earlier full-gate attempts exposed stale test patch locations, which were fixed,
and then two pytest temporary-directory creation errors. A repository-local temporary directory also triggered hygiene tests that intentionally exclude `.scratch`; it was replaced with isolated `/tmp/fleet-prd2-tmp`. An interrupted run was repeated. Earlier failed/interrupted attempts are not counted as passing checks.

Repeated `/files` operations reused host Sandbox `d65b4b90-4108-4f97-ba9c-3bc00f88016f` across independent provider inventories. `/exit` completed graceful application shutdown. Independent SDK probes confirmed that host Sandbox and root `31f28177-e8c0-43b3-9446-4695f0407bb2` were absent. Resume command: `uv run fleet cli --port 8012 -- --session ab9ac10c-7036-47fb-857e-edd7fd6a83c7`.

The first resumed request failed during host acquisition before execution/start delivery; its Run `21b38ac7-e92a-48db-aa2b-342a5b6f713b` remained failed without a commit. Startup orphan cleanup also reached its existing 60-second bound. A subsequent request succeeded with unchanged settings. These transient provider/acquisition failures are recorded, not presented as successful executions.

The additional child was independently observed as `01cb0388-465e-4431-ab07-c9b8c32bcc5c`, running snapshot `fleet-rlm-python313-child-v5`, profile `semantic-child`, with zero Volume mounts. Independent SDK `get` after completion confirmed absence. The complete combined trace `tr-298fcc4e333f9a43bd4e14eae2639169` (Run `098d24c7-5068-4948-8a19-b256f82514b1`) is OK: seven `sandbox.execute` spans, one write and two read tool invocations, one recursive child, confirmed child cleanup, completed commitment and completed Turn cleanup. All spans have end times. SQL commit revision 5 and released claim independently confirm settlement. Child output reports marker, real 30-second sleep, sum 24 and root file invisibility.

After successful final Turn completion, the new root was still running with autostop restored to 15 minutes, one intended Volume mount, and no held SQL claim. Independent SDK probing confirmed host `b78825de-6550-4cf2-abfb-53322eb69121` absent after idle expiry.

Final `/exit` completed application shutdown with no retained-ownership or unresolved-cleanup warning. Independent SDK probes initially observed the root in a deletion transition (`unrecoverable`); this was not accepted as absence. A subsequent probe confirmed root `fc0eb961-97f4-43c0-9668-61e79e66954b`, child `01cb0388-465e-4431-ab07-c9b8c32bcc5c` and host `b78825de-6550-4cf2-abfb-53322eb69121` all absent. Earlier root and host identities were also confirmed absent. No validation Sandbox or executing CLI remains. Committed validation Session/file data remain for durability replay.

Coverage is full for this agreed PRD 2 lifecycle implementation. PRDs 3, 8, 9 and 12 receive only the bounded contributions named above; broader durability cutover, trust-policy redesign, observation policy and repository-wide simplification remain unimplemented by this change. Historical rendered review artifacts remain baseline snapshots.

The final combined Turn took 7m37s, largely waiting for model actions; its TUI reported zero execution errors. The read-only child inventory watcher emitted an OpenTelemetry context-detach warning when ending SDK iteration early. The Sandbox observation and later absence probes succeeded; the product trace independently had every span closed. That SDK/probe observation is retained as a separate follow-up, with no dependency or observation-policy change here.
