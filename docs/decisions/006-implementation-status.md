# ADR 006 implementation status

This is the execution ledger for [ADR 006](006-native-turn-scoped-runtime-and-evaluation.md),
through Phase 6 inclusive. It records implementation separately from certification.
The starting checkout was `0603e15a1d7ad5a10c8ed30e3fb9f2773569551d`.

## Current work

This ledger retains dated implementation and validation results. Counts, SHAs,
local environment observations, and receipt paths below describe those runs;
they are not a fresh certification of every later checkout. Read current policy
from `config/fleet.toml` and rerun the relevant lane for a new candidate.

The Phase 6 corpus at `scripts/benchmarks/phase6_cases.json` is currently checked
as a fixture only. The maintained quality dataset and ingestion path still use
the five `QUALITY_RECORDS`; corpus integration and per-case experimental
classification must be completed before claiming a Phase 6 quality campaign.

### Phase 4 exit (2026-09-12)

Checkout `81ff6f84e` plus harness/docs. Continuation receipt
`.scratch/benchmark-reports/phase4-ablation-20260912-continue.json`:
**144/144** admissions, service cleanup confirmed, no safety halt.
Original mechanical `phase4_decision` = **`incomplete`** (14 completed frozen-C
rows lack call-shape usage in SSE). Trace closeout overlay
`.scratch/benchmark-reports/phase4-ablation-20260912-overlay.json` backfilled
all **14** C-gap rows from local MLflow (`http://127.0.0.1:5001`, experiment
`fleet-rlm`); overlay `phase4_decision` = **`disable`**. Retain is
independently impossible: bootstrap CI lower bound **0.0**. P4.6 applied
**disable**: `[defaults.rlm] recursion_enabled = false`. Native `llm_query`
remains.

Value-proof memo (trace closeout):
`.scratch/benchmark-reports/phase4-value-proof-20260912.md`. P4.6 disable memo:
`.scratch/benchmark-reports/phase4-p46-memo-20260912.md`. Trace sample (**49**
traces): zero `child_lm_calls` > 0, zero `RLM.recursive_call` / `rlm_query`
spans, zero `sub_lm_calls_depth_0` (native `llm_query` also unused on the
sealed 12-case corpus). Children were offered on arms C/D but not required by
the corpus; this ablation does not test depth-1 need on long-context work.
Phase 5 (snapshot promotion, deployed Lakebase closeout, MLflow backend cert)
is **not started**.

The simplification-plan phases in
[`fleet-rlm-implementation-plan-2026-09-06-v2.md`](../../fleet-rlm-implementation-plan-2026-09-06-v2.md)
are the forward sequence. The "Implemented through Phase 6" checklist later in
this file uses the older ADR 006 numbering and is not that sequence.

### Phase 3 recert, P4.5 harness fix, and docs reconcile (2026-09-12)

Checkout `81ff6f84e` plus the P4.5 admission-fault harness (empty control-case
attachments were HTTP 400 / vacuous-cleanup halt). Recursion default was
unchanged until the 144-row continuation above authorized P4.6.

- **Phase 3 complete-MVP recert** on `81ff6f84e`:
  `.scratch/live-receipts/mvp-complete-20260912.json` — `passed: false` at
  `first_turn`. `verify_semantic_work` ran; `append_workspace_text` did not.
  Assertions were not weakened. A steering-only rerun
  (`.scratch/live-receipts/mvp-complete-20260912-r2.json`) failed earlier with
  `Turn output is invalid` after SUBMIT of unbound names; that steering change
  was reverted. Follow-up product fix (this checkout): Root instructions no
  longer treat a verification helper as license to `SUBMIT` while named
  host-tool writes remain; registered write/publish tools inject that
  fragment. Complete-MVP live wrap-up reserve is 60s so wrap-up cannot rewrite
  the required third cell.
- **Phase 3 complete-MVP recert** on `8a2b20277` (instruction/wrap-up fix plus
  P4.6 disable): `.scratch/live-receipts/mvp-complete-20260912-r3.json` —
  `passed: false`, `receipt_invalid` / `durability_receipt`. Lane 1 pytest
  returned 0 (~105s), so B5 likely ran, but
  `scripts/live_daytona_verify.py` then read
  `.scratch/clean-backend-refoundation/assets/live-b5-attachment-artifact-durability-evidence.json`
  while the durability test writes
  `.fleet-evidence/receipts/p35d/live-b5-attachment-artifact-durability-evidence.json`.
  Lane 2 (`test_complete_daytona_mvp_through_fastapi`) did not start.
  Assertions were not weakened. Path aligned on `47df9cbb7`.
- **Phase 3 complete-MVP recert** on `47df9cbb7` (B5 evidence path aligned):
  `.scratch/live-receipts/mvp-complete-20260912-r4.json` — `passed: false`,
  `proof_failed` / `fastapi_dspy_daytona_mvp`. Lane 1 durability passed; the
  verifier now reads the p35d receipt path. Lane 2 failed at `first_turn`.
  Diagnostic pytest (same SHA, logs the verifier discards):
  `.scratch/live-receipts/mvp-complete-20260912-r4-pytest.json` /
  `mvp-complete-20260912-r4-pytest.log`. SSE `tool-input-available` names were
  `issue_iteration_token`, `llm_query`, `llm_query_batched`,
  `verify_semantic_work` — still no `append_workspace_text`. Root **did**
  emit `append_workspace_text` / `publish_workspace_artifact` in the second
  cell after the instruction fix, and wrap-up 60s did **not** steal cell 3.
  Execution never reached the write: Root paraphrased the mandated Sub-LM
  prompts (`Return exactly ROOT` / `ALPHA` / `BETA` / `GAMMA`) and omitted
  `accumulator.extend`, so `verify_semantic_work` raised "batched semantic
  results are out of order" and the cell aborted before the append. Root then
  SUBMIT-ed without retry, matching the prompt's "do not retry" clause.
  Assertions were not weakened. Recursion stays disabled. Phase 5 is not
  started. Verbatim-prompt overlay landed on `36792ac8`.
- **Phase 3 complete-MVP recert** on `36792ac8` (copy request-specified Sub-LM
  prompts; `long-context` 2.0.1): `.scratch/live-receipts/mvp-complete-20260912-r5.json`
  — `passed: false`, `proof_failed` / `fastapi_dspy_daytona_mvp`. Lane 1
  durability passed. Lane 2 failed at `first_turn`. Diagnostic:
  `.scratch/live-receipts/mvp-complete-20260912-r5-pytest.json` /
  `mvp-complete-20260912-r5-pytest.log`. SSE names were
  `issue_iteration_token`, `llm_query`, `llm_query_batched` —
  `verify_semantic_work` count 0, still no `append_workspace_text`. Root
  again planned append/publish in cell 2 but paraphrased the mandated
  prompts (wrong strings, two batch items not three, positional
  `verify_semantic_work`, no `accumulator.extend`). Cell 2 then hit
  Sub-LM HTTP read timeouts, so verify and the named write never ran.
  Root SUBMIT-ed on iteration 3 without retry. Assertions were not
  weakened. Recursion stays disabled. Phase 5 is not started. Further
  instruction overlays on this Root model are not getting exact prompt
  copy; a later recert needs a different product lever, then a new
  `live_daytona_verify.py` run. r5 closed the live loop on this Root.
  Phase 3 complete-MVP stays **uncertified**. The Sub-LM HTTP timeout
  isolation that followed is a separate product fix: native Sub attempts
  keep `llm.sub.timeout_seconds = 90` as a hard ceiling after
  `bind_turn_deadline`, remaining Turn time may only shrink it, and
  provider retries share that ceiling so one packed `llm_query` then
  concurrent `llm_query_batched` cell can finish under
  `rlm.execution_timeout_s = 300` without stacking `90 + 90` per call.
  Root/Sub model ids, MVP assertions, and `execution_timeout_s` are
  unchanged. That timeout work does **not** certify Phase 3. Do not
  patch Root instructions again or run another paid recert shot on this
  model unless a later model or contract change justifies it. Phase 5
  is not started.
- **Phase 3 complete-MVP recert** on `4cf4ee38` (Sub-LM HTTP timeout ceiling):
  `.scratch/live-receipts/mvp-complete-20260912-r6.json` — `passed: false`,
  `proof_failed` / `fastapi_dspy_daytona_mvp`. Lane 1 durability passed.
  Lane 2 failed at `first_turn`. Root again planned append/publish in cell 2
  but paraphrased the mandated Sub-LM prompts (`Summarize the Daytona MVP
  proof…` / `What is Daytona?` instead of `Return exactly ROOT` /
  `ALPHA`/`BETA`/`GAMMA`, two batch items, no `accumulator.extend`). The
  invented Sub call then hit `Turn LM deadline exceeded` (~90s). Timeout
  isolation is in place and is **not** Phase 3 certification. Diagnostic
  pytest on the same SHA also failed (`verify_semantic_work` count was not
  1). Assertions were not weakened. Recursion stays disabled. Phase 5 is
  not started. The follow-up product lever restores request-specified
  `llm_query` / `llm_query_batched` string literals at Daytona execute
  rather than overlaying Root instructions again.
- **P4.5 halt cause:** control cases upload empty attachments; Fleet rejects
  empty files with HTTP 400; `_error_observation` treated that as unconfirmed
  cleanup and halted at 109/144. The runner now skips empty uploads, confirms
  vacuous cleanup for pre-turn 4xx, and `--continue-from` retries those faults.
- **P4.6:** no default/profile change from the incomplete 109-row receipt
  (superseded by the 144-row disable in the section above).
- **P5.1:** v10/v5 remain probed candidates; configured/code fallbacks stay
  v7/v2. Warm pool remains disabled. Phase 5 is not started.
- **P6.5:** `ARCHITECTURE.md`, this ledger's numbering note, and DSPy mapping
  docs now describe fresh-per-Run programs on the retained broker path.

### Phase 3 re-cert and P4.5 live campaign (2026-09-11)

Operator-gated live work on `fix/adr006-runtime-continuation` with MLflow
`3.16.0` on canonical store `http://127.0.0.1:5001`.

- **Phase 3 lane 2 (MVP complete):** receipt
  `.scratch/live-receipts/mvp-complete-20260911.json` — `passed: false`.
  The turn reached `stop`, but the model did not invoke `verify_semantic_work`
  before workspace publish; semantic-step contract remains open.
- **Phase 3 lane 3 (failed-run discard):** receipt
  `.scratch/live-receipts/memory-failed-run-20260911-failed-run.json` —
  `passed: true` after `rlm_wrap_up_seconds=0` and explicit budget-directive
  override in the live prompt.
- **P4.5 partial-live smoke:** receipt
  `.scratch/benchmark-reports/phase4-partial-live-20260911.json` — ten sealed
  exploratory rows (A3/B3/C2/D2), all cleanup confirmed, MLflow trace IDs on
  every row; mechanical `incomplete` as expected.
- **P4.5 full live ablation:** receipt
  `.scratch/benchmark-reports/phase4-ablation-20260911.json` — **109/144**
  admissions before halt (`cleanup_failure` / `unconfirmed_cleanup` on trial
  B-p4-control-01-r1 with `http_400`). Charged spend **$5.56** (under cap).
  Mechanical `phase4_decision` → **`incomplete`**.
- **P4.6 recommendation memo:** `.scratch/benchmark-reports/phase4-p46-memo-20260911.md`
  — **do not retain** simplified recursive profile as default; re-run remaining
  admissions after fixing the late admission fault. **No default/profile change**
  until operator approves a complete receipt.

Harness fixes landed in `2aee1c93`: MLflow 3.x search preflight, shielded
turn-root close, orphaned-sandbox sweep, extended telemetry wait.

### Phase 0 stabilization baseline (2026-09-10)

- P0.1: the deterministic unit lane initially failed because the editable
  `config/fleet.toml` no longer carried its canonical-policy comment, which
  the comment-preserving policy writer must preserve. The comment was restored
  in the source policy; the assertion was retained as the regression contract.
- P0.2: MLflow trace content is bounded and readable by default, including
  provider reasoning/chain-of-thought and system-prompt fields in the authorized
  engineering trace destination. Set `mlflow.trace_content_enabled = false`
  for operational-only traces. Public typed Runtime Events remain a separately
  bounded API/SSE contract and do not inherit MLflow capture policy.
- P0.3: until the Phase 1 containment decision, do not add runtime variants,
  recursion-depth or Tool families, warm-capacity behavior, persistence tables,
  or feature flags. Route correctness work through existing owners.
- P0.4 baseline, collected from tracked Python files (excluding `__pycache__`):

  | Metric | Baseline |
  | --- | ---: |
  | `src/fleet_rlm` Python files | 174 |
  | Unit / contract / e2e / freeze Python test files | 251 / 37 / 2 / 3 |
  | Deterministic unit collection / wall time | 2,752 selected (84 deselected) / 27.534 s (`make test-unit`, Apple Silicon local) |
  | Source bytes | 2,567,700 |
  | Production-selectable runtime variants | 1 (`legacy`) |
  | Model-facing recursive Tool names | 5 (`rlm_query`, `rlm_query_batched`, and three capsule migration variants) |
  | Explicit scheduler/executor-owning modules | 7 (`rlm`, `daytona`, and MLflow owners) |

  Largest source modules: `rlm/session_runtime.py` (115,129 bytes),
  `runtime/daytona/run_environment.py` (103,874), `workspace/storage.py`
  (103,767), `daytona/session_manager.py` (102,336), `rlm/recursion.py`
  (101,675), `workspace/memory.py` (96,111), `daytona/broker.py` (94,567),
  `rlm/runtime.py` (86,756), `chat/turn_runtime.py` (63,596), and
  `daytona/runtime.py` (63,425). Re-run this same inventory after the Phase 2
  and Phase 3 deletion work; it is a baseline, not a quality claim.

### Phase 1 containment decision (2026-09-10)

- P1.1 reduced the provider proof to
  `tests/live/backend/test_daytona_containment.py`. It creates one disposable
  sandbox/context, starts an ordinary child and a detached process-session
  child, deletes the context, observes their markers, then verifies whole
  Sandbox deletion. Four operator-gated runs retained bounded receipts at
  `.fleet-evidence/receipts/adr006/p1-containment-20260910T074746.json` and
  `p1-containment-repeat{1,2,3}-20260910T074746.json`.
- P1.2 inspected the pinned Daytona `0.210.0` public interpreter surface:
  `create_context`, `delete_context`, `list_contexts`, and `run_code`. Its
  smallest relevant cleanup operation, `delete_context`, is not process-tree
  containment: all four runs observed the ordinary child completing, context
  deletion succeeding, the detached child surviving, and the disposable
  Sandbox subsequently absent. Process/PTY session controls cannot target a
  Code Interpreter context, so no provider-supported whole-tree mechanism is
  available for reusable native contexts in this pinned SDK.
- P1.3 ran the existing whole-Sandbox lifecycle benchmark. The sealed receipt
  `.fleet-evidence/receipts/adr006/p1-turn-scoped-benchmark-20260910T074812.json`
  completed all 20 measured deletion checks, but its create-through-first-
  execution p95 was 33.296085 s against the 10 s threshold. It therefore
  selected `retained_session`, not a Turn-scoped Sandbox. The benchmark emitted
  broker-cleanup-pending warnings during interpreter shutdown; Sandbox deletion
  still completed and this observation remains a Phase 2 cleanup-owner concern.
- P1.4 decision: **B — retained broker execution boundary**. Option A is
  rejected because native context deletion does not contain detached process
  sessions. Option C is deferred because its matched lifecycle lane misses the
  approved latency threshold despite confirmed whole-Sandbox deletion. Rollback
  is the previous complete release/config/image/database-compatible state; it
  does not retain a second runtime in one process. The legacy broker remains
  the sole selectable runtime while Phase 2 removes only owners that are
  independent of this selected boundary.

### Phase 2.1 provider package consolidation (2026-09-10)

- Removed the duplicate `runtime/daytona/` package. Turn preparation now lives
  in `composition/daytona_run_preparation.py`; composition retains the Daytona-backed
  Workspace gateway wiring in `composition/daytona_workspace_gateway.py`.
  Composition, deterministic/live tests, release validation, and source-layout
  documentation were updated accordingly.
- This is a package-boundary consolidation only. It does not add an owner or
  alter broker execution. P2.2 remains responsible for reducing the lifecycle
  owner overlap still present in `composition/daytona_run_preparation.py`,
  `daytona/session_manager.py`, and the lease/cleanup helpers.

### Phase 2.2 resource-ownership reduction (2026-09-10)

- `DaytonaRuntimeResources` now owns its late sandbox-cleanup tasks,
  client-close task, and provider-retained environment providers. Composition
  observes that exact resource owner during deferred shutdown; it no longer
  consults process-global Run-environment task collections.
- Session-manager lease ownership and root-replacement collections remain
  intentionally unchanged for the next P2.2 migration. Broker execution and
  shutdown semantics are unchanged.

### Phase 2.2–2.7 retained-broker continuation (2026-09-10)

- P2.2: late acquisition/lease records now belong to the existing Session
  manager, with no module-global maps or redundant manager back-references.
  Shutdown cannot report success while an unscheduled foreign-loop acquisition
  remains owned. Active Session claims are now owned by each
  `DaytonaSessionManager`, rather than a process-global registry; release,
  pre-warm, idle-stop, and late-cleanup checks use that same owner. Production
  root acquisition now delegates directly to `DaytonaRuntime`, which solely
  owns root replacement and cleanup; the adapter's remaining local index is
  compatibility-only and is not used by composed Daytona runtime.
- P2.3: complete. Production RLM execution builds a fresh DSPy program,
  direct Tool bindings, callbacks, and worker executor for each Run.
  Sequential-Run coverage proves durable committed history, not Python state,
  is the cross-Turn input. `DaytonaRuntime` retains broker roots independently.
  The resident registry, program fingerprints, generations, and stable Tool
  proxy rebinding were deleted with their integrations and direct tests.
- P2.4: deleted the native Turn preparation branch, duplicate runner worker
  and lease implementation, plus the production-composition native context
  factory and cancellation owner. Execution contexts reject unselected
  variants. The unreferenced `NativeInterpreterBackend` feasibility probe,
  native binding-watch support, benchmark, and direct tests were deleted, so
  the broker is the sole code-execution implementation in the source tree.
- P2.5: Fleet's JSON repair/finalization adapter moved to `rlm/program.py`;
  sync and async drivers retain one shared policy. Field insertion is shared,
  budget accounting stays in `budget.py`, and pinned DSPy marker/type/callback
  adaptation remains in `compat_3_3_1.py`. No model, version or retry-policy change.
- P2.6: complete. The pinned Daytona SDK capability gate proves filesystem
  download/list APIs lack Fleet's bounded cursor controls, while upload/delete
  expose no append, patch, checksum/CAS, or atomic-publication contract. The
  operation audit therefore retains the Workspace Agent as the sole filesystem
  semantics owner; no custom operation exists only for historical reasons.
- P2.7: future image definitions remove DSPy; Session/WorkspaceChild retain
  analysis packages, SemanticChild adds no Python packages. The runtime probe
  checks imports and exact versions from the selected profile. A standard-library-only
  subprocess executes broker setup, committed history, attachment reconstruction
  and typed SUBMIT with no DSPy available. New immutable Session
  `fleet-rlm-python313-v10` and SemanticChild `fleet-rlm-python313-child-v5`
  images were created and their runtime probes passed; each disposable probe
  Sandbox was deleted. Existing names and configuration remain rollback
  references. The opt-in aggregate verifier now
  checks/probes both candidate images, then runs the narrow Session host-tool/RLM
  stream proof and SemanticChild recursive lane through verifier-only Settings
  overrides; it writes one bounded receipt and never changes the configured
  references.
  The earlier 2026-09-10 candidate run remained non-promotable: image probes
  passed, but the Session MVP failed during its first RLM Turn, before recursive
  evidence. The focused replacement certification then passed on candidate
  `caa4fd83b578f610e28f6f0e792b04aa0da41d9e`: both immutable probes, the
  Session host-tool/RLM stream proof, and SemanticChild recursive proof passed
  with disposable cleanup confirmed. The sealed bounded receipt is
  `.fleet-evidence/receipts/adr006/p27-reduced-snapshots-20260910-r4.json`.
  P2.7 is complete; manual promotion remains a separate operator decision.

The Phase 1 retained-broker decision remains authoritative. This continuation
does not complete Phase 2 or certify native containment, filesystem SDK parity,
remote image behavior, or live model quality. Independent reviewer acceptance
is still required.

Validation for this continuation:

- Focused manager/prewarm, adapter/budget/factory, resident reuse and Turn
  preparation tests passed. Snapshot-definition/probe, history/context,
  socket-free broker and adapter benchmark replay regressions passed.
- Unrestricted `make check` did not pass: loopback binding is prohibited in
  this workspace sandbox, causing the broker network test and 11 CLI supervisor
  tests to fail. A stale benchmark adapter import discovered in that run was
  corrected and its two replay tests passed afterward.
- `UV_CACHE_DIR=/private/tmp/fleet-uv-cache UV_NO_SYNC=1
  PYTEST_ADDOPTS='--ignore=tests/unit/backend/test_cli_supervisor.py -k not\ co_located_worker'
  make check` passed with **78.09% backend coverage** and **543 TUI tests**.
  This excludes the entire CLI supervisor module and the broker network test;
  it is a restricted local gate, not an unrestricted pass. Lint/format/type,
  API/generated client, source-tree, dependency-boundary and docs checks passed.
- Credential-free image plans generated for proposed v10/v5 names. No image
  creation, live provider/model/database lane, deployment or promotion ran.
- `git diff --check` passed and the staging index remained empty.

### MLflow 3.16 continuation

Baseline: `063bea648`. Full completion remains the target. The 2026-09-08
local-gate and live-receipt continuation is recorded below; execution order:

- [x] Reconcile Phase 1 evidence and complete the exclusive PostgreSQL campaign.
- [ ] Certify the remaining MLflow 3.16 fault-injection and configured-backend lanes.
- [ ] Require confirmed whole-sandbox deletion for native execution until stop/start containment is certified; then prove settlement and durable continuity.
- [ ] Complete remaining profile/mount certification, optional capacity and filesystem parity. The wheel/sdist artifact matrix is complete; provider profile and warm-pool gates remain open.
- [ ] Certify the implemented read-only partial recursion and complete matched quality/cost evidence.
- [ ] Complete shared evaluation, real GEPA, immutable promotion and rollback.
- [ ] Remove legacy machinery only after the corresponding certification gates pass.

The exclusive PostgreSQL campaign used a newly provisioned disposable database
and is retained at
`.fleet-evidence/receipts/adr006/postgres-contention-fleet_rlm_cert_5049b32b8ba0.json`.
It proves all six contention scenarios, disjoint outbox ownership, Alembic head
`019fe0010001`, server version `170011`, and representative Session/history,
reconciliation/recovery, replay, and outbox plans. The database was removed after
the receipt was sealed. The MLflow certification lane currently targets the
explicit local 3.16 server; the `MLFLOW_TRACKING_URI` Databricks override was
removed from the local environment, so no configured managed-backend claim is
made. Keep native production, paid capacity and program promotion disabled while
their independent gates are open. Existing feedback API/TUI support is retained;
feedback does not by itself establish a verified expected answer.

### Ordered continuation

The corrected Phase 1 contention lane passed all six scenarios, including
disjoint outbox ownership, on the exclusive disposable PostgreSQL target. The
sealed receipt records target/version identity, exercised scenarios, projected
plans, and cleanup. The SQLite writer-lock correction remains covered by the
focused local lane; it does not replace PostgreSQL row locking or weaken state
checks.

The local MLflow 3.16 receipt is
`.fleet-evidence/receipts/adr006/mlflow-local-certification-20260908T1858.json`.
It proves the local backend, DSPy autolog plus `DeadlineLMProxy`, async root and
child traces, sanitization, feedback rationale, trace linkage, concurrent
Sessions, repeated lifespans, and fail-soft unreachable-backend handling. Token
usage and the injected outage/credential/saturation/slow-flush cases remain
explicitly unknown or unexercised, so the receipt is not promotion evidence.

The live Daytona MVP sample
`.fleet-evidence/receipts/adr006/mvp-20260908T1905.json` is retained as a
failed semantic-quality receipt. Its first Turn could not prove accumulator
continuity after the model's native follow-up, so it does not establish durable
continuity or a legacy quality baseline. The failure remains visible for the
matched-campaign gate; no retry or threshold adjustment is treated as a pass.

Phase 2 implementation audit (2026-09-08): the complete
`tests/unit/backend/rlm` suite collected 551 tests and passed. It exercises the
single program/adapter construction seam, shared budget admission and
root-only finalization capacity, sync/async adapter repair, immutable LM
templates and proxy copies, native history separation, tool namespace and
callback contracts, model-role attribution, usage reconciliation, and
Runtime Event/tracing independence. The Phase 2 checklist is marked complete
for these executable implementation contracts; provider-backed semantic
quality and matched native-versus-broker performance remain later evidence
gates.

The consolidated plan remains ordered by Phase 1, 1.1, 2, 3, 4, 5 and 6. The
earlier Phase 2/3/6 fixes remain retained, but do not establish completion of
the preceding phases. The 2026-09-08 continuation now closes the corrected
exclusive PostgreSQL campaign and retains a bounded local MLflow receipt; all
remaining provider, semantic-quality and rollout gates stay explicitly open.

- P1A.01 repository inventory: one head, `01a087800002`, with linear ancestry
  through additive `01a087800001`, `019fe0010001`, `019fdb010001`,
  `019fa2e4b7c1`, `019f8c1d2e3f`, `019f7950a1b2` and baseline
  `019f5b3c96bd`. No repository merge revision is indicated. Deployed database
  heads have not been inspected.
- The focused binding/Turn lineage migration, database compatibility, claim
  constraint classification and claim adapter parity lane passed 32 tests.
  This is local evidence only; P1A.01 deployed reconciliation remains open.
- P1B.02 now has query counts and SQLite EXPLAIN coverage for Session listing,
  history, claim-conflict replay, recovery and outbox claims. The exclusive
  disposable PostgreSQL receipt also retains bounded plans for all five paths;
  its workloads are synthetic fixtures, so representative deployed workload
  measurements remain open. Removed the unused artifact lookup in replay (four
  statements reduced to three).
- P1B.03 recovery tests verify no checked-out connection at the provider fence
  on either success or failure. P1B.04 now records bounded operation timings
  and outcomes after facade transaction scope exits, with fail-soft logging
  and optional existing trace spans. Arguments, results and exception text
  are excluded; privacy, cancellation and broken-sink regressions pass.
- Earlier validation covered 37 focused persistence tests and a passing local
  repository gate. The current gate result is recorded above with 78.61%
  backend coverage and 543 TUI tests. Phase 1 remains open for deployed
  database evidence; later phases are not promoted by either local or
  disposable-target results. Progress is checked in the consolidated plan.

### Phase 1–6 continuation (2026-09-08)

The continuation starts at `a1957d2ac`. Existing mechanics through Phase 6
do not mean that every local implementation task is complete. The unchecked
tasks in the consolidated implementation plan remain open until their specific
implementation and evidence requirements are satisfied.

- Phase 3 output admission now checks completed SDK stdout, stderr and error
  bytes independently of streamed callbacks, before final-output parsing.
  Replay regressions cover omitted callbacks, UTF-8 byte limits, retained
  containment ownership and avoiding duplicate streamed/result accounting.
- Phase 3 broker workers now transfer cancellation and process-control
  exceptions to their owning caller without converting them into ordinary
  execution failures. Interrupted workers skip the post-execution callback
  drain. Regression tests verify exception identity, completed worker ownership,
  no drain after interruption and isolation from the next invocation. This is
  local cancellation propagation evidence, not remote termination proof.
- Phase 6 evaluation rejects malformed or blank evidence requirements, matches
  complete evidence identifiers and uses tool outputs rather than trace
  attributes. This is evidence-presence validation; it does not establish
  answer correctness or semantic support.
- These changes do not complete database certification, native settlement
  integration, warm-pool reconciliation, matched recursive campaigns or cutover.
- Continuation validation: 28 focused tests passed; `make check` passed with
  78.47% backend coverage and 538 TUI tests, including generated contracts,
  type/lint/format, dependency boundaries and documentation checks. No live
  certification lane was run.

### Benchmark campaign recording (2026-09-09)

- P1.1B-M.01–04: `scripts/benchmarks/record_mlflow_campaign.py` now turns one
  sealed `fleet.runtime-benchmark/v2` or `fleet.runtime-adapter-comparison/v2`
  receipt into an explicit MLflow tracking run. It requires `FLEET_LIVE=1` and
  an explicit experiment/`--purpose`, verifies the receipt digest before
  recording, logs identity parameters/tags, records full-run metrics with
  sample and failed-sample denominators (absent values become
  `fleet.campaign.metrics_unknown`, never zero), uploads the sealed receipt as
  one artifact, and fail-closes `fleet.campaign.promotion_eligible=false` for
  scripted receipts. Unit tests cover tamper rejection, unknown-not-zero,
  denominators, per-variant/gate projection and the live gate; running the
  bridge against a configured backend remains an operator action and no
  campaign evidence is claimed.

### MLflow export, isolation, and purpose continuation (2026-09-09)

- P1.1C.09: `tests/unit/backend/test_mlflow_export_outage.py` certifies the
  real MLflow 3.16 export machinery under deterministic fault injection:
  outage and sequential cycles stay non-blocking within the per-Turn budget,
  expired credentials record ERROR drop evidence, queue saturation drops
  without blocking, stalled shutdown flushes stay observable, and no span
  content reaches failure logs. Managed-backend lanes remain operator actions.
- P1.1C.10: repeated-lifespan, config-restore, and cross-Session isolation
  behavior is certified by the existing runtime suites plus a new
  consecutive-Session tag-contamination test; evaluation/optimization jobs stay
  in separate operator processes with explicit experiment selection. No live
  MLflow backend was exercised.
- P1.1C.11: `mlflow_experiment_purpose` records a `fleet.experiment.purpose`
  tag on the configured experiment at configure time; a conflicting recorded
  purpose fails configuration and tag-write failures stay soft. The runtime
  default is `runtime` in `config/fleet.toml`; campaigns keep explicit
  experiment/purpose selection. No managed experiment was recreated and no
  Unity Catalog trace location moved.
- P1.1C.12: existing suites certify the separation. The session-scoped
  feedback route maps cross-session trace mismatches to the closed 404, and
  `observability/feedback.py` re-verifies the `fleet.session_id` tag; SSE
  emits the trace ID only as correlation and omits it when not captured. No
  public route offers trace lookup by ID, and clients stay functional through
  the closed-lifecycle 503 when tracing is disabled.
- P3B.05: the broker's identity-backed retry admission now has its regression
  proof. `register_tools` defaults to an empty retryable set, rejects
  `fetch_url`/non-read-only opt-ins and unbound names with
  `InvalidToolPolicyError`, and accepts only explicit read-only subsets; the
  host re-derives and compares the content-derived request key before any
  dedupe claim. Four new unit tests join the existing dedupe, concurrent-wait,
  forged-key, and write-exclusion suites.

### Root reserve continuation after `4f81687f`

- Phase 2 late-response reclassification now respects the call-local LM's
  finalization capability. Child responses use their local wrap-up allowance
  without debiting the root-only finalization reserve or charging another
  provider admission.
- Regression tests reproduced the reserve loss before the fix in sync and
  async adapter calls. Coverage includes late valid submission, late parse
  failure followed by repair, local attempt limits and settlement rejection.
- This closes the demonstrated accounting defect; broader Phase 2 accounting
  certification and Phase 1–6 implementation remain open.
- Validation: 40 adapter integration tests passed. `make check` passed on
  rerun (78.45% backend coverage, 538 TUI tests). The first full run failed
  the existing 50 ms recursive batch startup/deadline assertion; that test
  passed in isolation and on the full rerun without changes. No live lane ran.

### Previously retained implementation

- Phase 0: ADR vocabulary and the three evidence lanes already exist. The runtime
  benchmark now captures installed Daytona, DSPy and MLflow identities.
- Phase 1: additive migration `019fe0010001` adds Sandbox Binding Workspace and
  composite Session/Workspace lineage, with follow-ups `01a087800001` for
  durable warm-pool ownership and `01a087800002` for monotonic binding
  generations. The Session status CHECK remains enforced. Dirty-data preflight
  runs before DDL. SQLite upgrade, enforcement, downgrade and row preservation
  tests exist. The exclusive disposable PostgreSQL receipt now retains six
  contention scenarios and five bounded query-plan paths; deployed heads and
  representative deployed workloads remain unverified.
- Phase 1.1: Daytona and its six generated clients are pinned/resolved to 0.210.0.
  DSPy remains 3.3.1; MLflow and its skinny/tracing distributions are now pinned/resolved to 3.16.0.
  The organization header compatibility code remains necessary in the installed
  SDK's API-key path. Typed file/process/daemon absence no longer implies sandbox
  absence, including after Fleet error normalization. Volume creation conflicts
  reconcile by lookup, and Volume failures cross the normalized error boundary.
  Benchmark comparison accepts one explicit runtime, SDK or
  snapshot axis while rejecting unrelated identity drift. MLflow 3.16.0 / OTel
  1.44.0 span export now clears content before restoring sanitized values;
  actual SDK exporter tests cover redaction failures, excess attributes,
  exception events and attachments. Async bridge tests retain parentage across
  sequential Turns without reusing trace IDs. Trace content is opt-in in resolved policy;
  previews follow that policy. Serving disables compilation/evaluation autolog.
  Queue, worker, retry and shutdown waits have explicit policy limits, and
  timed-out flush work remains observable through its lifecycle owner. The
  local MLflow 3.16 receipt now covers core tracing, linkage, sanitization,
  feedback rationale, concurrent Sessions and repeated lifespans; token
  aggregation, exporter fault-injection/settlement cases, managed-backend
  certification and a passing legacy semantic baseline remain pending.
- Phase 2: existing factory, tool catalog, output contract, budget and LM proxy
  remain the owners. No execution-core replacement has been made in this work.
- Phase 3: replay tests exercise the installed 0.210.0 interpreter with a mocked
  WebSocket. Output accumulates even with a callback; callbacks are not awaited;
  callback failure closes the socket. These observations do not prove remote
  termination. `NativeInterpreterBackend` now consumes an asynchronously acquired
  explicit context through the existing sync SDK view, interpreter/output owner
  and callback gateway. Replay covers iteration state, fresh-context isolation,
  typed SUBMIT, host tools, output overflow, authority and retained cleanup.
  The pinned DSPy 3.3.1 RLM/CodeInterpreter/SandboxSerializable sources were
  rechecked on 2026-09-08; native type routing is centralized in
  `rlm/compat_3_3_1.py`, and async host Tools use the composition bridge when
  DSPy runs synchronous interpreter actions from `RLM.aforward`.
  The host callback loop checks authority even for silent executions. Late SDK
  workers cannot publish execution statistics, and broker cleanup retains them
  until they exit. HTTP polling retains its existing bounded timeout, so this
  is not a claim of instantaneous cancellation at the absolute deadline.
  Caller-provided containment is mandatory. The public runtime policy exposes
  only `legacy`; native construction is an explicit feasibility seam that
  taints and closes its exact root owner before a preparation gate can release.
  The opt-in 2026-09-08 live feasibility lane now certifies the real
  preview/polling gateway topology, nested root -> host callback -> child ->
  root-resume path, typed native/broker output parity, authority-loss
  cancellation, separate-session concurrency and disposable cleanup. Its
  detached-subprocess probe is intentionally negative: a marker survived
  context deletion, so native production remains a no-go and the exact root is
  quarantined while the broker stays retained. Native context creation
  cancellation/timeout is covered by the provider root-lease tests, including
  late context deletion before gate release.
- Phase 4: a reproducible, non-secret `DaytonaEnvironmentManifest` now describes
  the three logical profiles. Session and Workspace children share the analysis
  image and Volume eligibility; semantic children have the lean image contract
  and are the sole generic-pool-eligible profile. The historical 2026-09-07
  `.env`-resolved Daytona 0.210.0 operator lane created and checked immutable
  v7/v2 identities and confirmed disposable no-Volume probe deletion. The
  configured v7/v2 identities still report image-definition drift and remain
  rollback references. New immutable identities `fleet-rlm-python313-v9` and
  `fleet-rlm-python313-child-v4` were created and passed actual-SDK disposable
  runtime probes (including DSPy import and cleanup) on 2026-09-10; sealed
  receipt retention and configuration promotion remain open.
  The prior v6/child-v1 identities remain immutable rollback targets. Volume
  mounts, pool reconciliation, backend capability checks and paid capacity
  remain unverified. The full doctor stopped earlier at the repository
  database/Alembic prerequisite; that is a separate unresolved live gate.
- Phase 5: generation-aware Session ownership, Session prewarm, disposable child
  leases and fenced cleanup remain the provider seams. Native RLM/context/binding
  mechanics remain feasibility-only; `runtime.variant` exposes only `legacy`
  until containment and continuity are certified. Live cutover, stop/start
  continuity, SDK I/O parity and resident/broker subtraction remain pending.
- Phase 6: `SubproblemCapsule` is a frozen, closed Pydantic model with deterministic
  JSON bytes, bounded fragments/references/output allocation, authorized-path
  checks and optional selected-file SHA-256 metadata. `execute_capsule` and the
  canonical `rlm_query_capsules_batched` path use selected input only, the
  SemanticChild profile, shared reservations, depth-one recursion and the
  application-owned bounded async scheduler. Typed per-child outcomes classify
  completed/failed/timeout/cancelled work. `rlm_query_capsules_batched` remains
  ordered and all-or-nothing. The separately named
  `rlm_query_capsules_readonly_batched` returns only bounded typed sibling
  outcomes for Root verification after ordinary child failure; it shares the
  same admission, authorization, cancellation and cleanup boundary, so those
  failures remain fatal. Legacy prompt tools remain available only for
  compatibility and still carry their historical Session snapshot; native root
  composition exposes capsule tools instead.

## Todo ledger

Checked items mean that the implementation or its local evidence is present in
this checkout. They do not promote the runtime, certify the provider topology,
or authorize paid capacity. **Numbering below is the older ADR 006 task list**
(database lineage as Phase 1, native interpreter as Phase 3, capsules as Phase 6).
It is not the 2026-09-10 simplification plan's Phase 0–6 sequence.

### Implemented through Phase 6

- [x] Phase 0 vocabulary, ownership boundaries, evidence-lane distinctions, and the maintained plan/status ledger are in place.
- [x] Phase 1 additive Sandbox Binding lineage and Session status enforcement are covered by upgrade, dirty-data, downgrade, and preservation tests.
- [x] Phase 1.1 Daytona 0.210.0 integration, typed error/resource-race handling, benchmark comparison axes, and MLflow privacy/lifecycle mechanics are implemented and locally tested.
- [x] Native-interpreter adapter/replay mechanics were implemented, then removed with P2.4; broker execution is the sole in-tree code-execution implementation. The 2026-09-08 native receipt remains a containment no-go.
- [x] Phase 4 Session/SemanticChild/WorkspaceChild manifests, profile contracts, operator plan/check/create/verify commands, and immutable snapshot receipts are retained.
- [x] Fresh per-Run RLM/program construction is production behavior on `legacy`; the native worker/lease branch is gone.
- [x] Bounded capsules, selected-input/path validation, depth-one scheduling, shared reservations, ordered typed sibling outcomes, and exactly two Root recursive tools (`rlm_query`, `rlm_query_batched`) are implemented.
- [x] Repository-wide local validation passed on earlier candidates (`make check`, coverage floor, generated-contract checks, TUI tests). Re-run the current checkout before treating those counts as fresh.

### Certification still open

- [x] PostgreSQL certification: the corrected exclusive campaign passed six contention scenarios and retained projected plans in `.fleet-evidence/receipts/adr006/postgres-contention-fleet_rlm_cert_5049b32b8ba0.json` (Alembic `019fe0010001`, PostgreSQL `170011`). The disposable target was removed after sealing. Deployed Lakebase closeout is Phase 5 and is not started.
- [ ] Live SDK/API-key, Volume, remote process containment, and stop/start or replacement continuity. The corrected Phase 3 receipt `.fleet-evidence/receipts/adr006/phase3-native-20260908T184525.json` still records a detached subprocess surviving context deletion; it is a provider-level native-production no-go, so `legacy` and the broker remain required. The durable Volume continuity receipt `.fleet-evidence/receipts/adr006/durable-continuity-20260908T1900.json` proves artifact readability/checksum across replacement, but does not prove native process containment.
- [x] Native interpreter production startup is withdrawn: P2.4 removed the in-tree native execution path. Broker remains the sole code-execution implementation.
- [ ] Warm-pool eligibility, quota, clean-instance, lifecycle, demand, and cost evidence; paid capacity remains disabled. Phase 5 owns any later canary.
- [ ] Complete MLflow exporter fault-injection, token-aggregation, and configured-backend certification. Phase 3 complete-MVP live quality remains failed. P4.5 matched ablation is retained separately and does not close MLflow certification.
- [ ] Phase 5 snapshot promotion, Phase 6 clean SHA / rollback rehearsal / GEPA. Not started.

## Evidence and next tasks

Executable evidence lives in:

- `tests/unit/backend/test_binding_lineage_migration.py`
- `tests/unit/backend/test_turn_lineage_migration.py`
- `tests/unit/backend/test_database_compatibility.py`
- `tests/unit/backend/test_daytona_platform.py`
- `tests/unit/backend/daytona/test_native_sdk_contract.py`
- `tests/unit/backend/daytona/test_sdk_resource_errors.py`
- `tests/unit/backend/daytona/test_native_interpreter.py`
- `tests/unit/backend/daytona/test_daytona_session_lifecycle.py`
- `tests/unit/backend/test_host_tool_submit_broker.py`
- `tests/live/backend/test_daytona_containment.py`
- `scripts/benchmarks/attach_phase3_receipt.py`
- `scripts/benchmarks/certify_mlflow.py`
- `scripts/benchmarks/certify_postgres.py`
- `tests/unit/backend/test_mlflow_export_privacy.py`
- `tests/unit/backend/test_mlflow_runtime.py`
- `tests/unit/backend/test_runtime_benchmark_v2.py`

SDK replay is adapter evidence, not live Daytona evidence. Scripted benchmark
keyword checks remain lifecycle smoke, not semantic-quality certification.
Production cutover, broker retirement and paid capacity cannot be certified by
these tests. The corrected Phase 3 receipt is retained, but its detached-process
probe remains a native-production no-go; the failed MVP semantic sample and the
local MLflow receipt likewise leave model-quality and exporter gates open.

The final local validation passed `make check` (exit 0, 78.61% backend
coverage; the 75% threshold was met), including generated API and stream
checks, 543 TUI tests, boundary checks and documentation checks. The retained
live receipts are scoped evidence for their individual lanes; they do not
certify the pending architecture or authorize native selection.

Remaining sequence:

1. Phase 5 when explicitly requested: snapshot promotion (v10/v5), deployed
   Lakebase inventory, configured MLflow, keep warm pool off.
2. Keep the proven broker route. Native production remains a provider no-go.
3. Phase 6 after Phase 5: clean SHA, rollback rehearsal, leftover deletion,
   docs numbering. GEPA only after that.
