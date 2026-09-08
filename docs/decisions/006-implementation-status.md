# ADR 006 implementation status

This is the execution ledger for [ADR 006](006-native-turn-scoped-runtime-and-evaluation.md),
through Phase 6 inclusive. It records implementation separately from certification.
The starting checkout was `0603e15a1d7ad5a10c8ed30e3fb9f2773569551d`.

## Current work

### MLflow 3.16 continuation

Baseline: `063bea648`. Full completion remains the target. The 2026-09-08
local-gate and live-receipt continuation is recorded below; execution order:

- [x] Reconcile Phase 1 evidence and complete the exclusive PostgreSQL campaign.
- [ ] Certify the remaining MLflow 3.16 fault-injection and configured-backend lanes.
- [ ] Require confirmed whole-sandbox deletion for native execution until stop/start containment is certified; then prove settlement and durable continuity.
- [ ] Complete remaining profile/mount certification, optional capacity and filesystem parity. The wheel/sdist artifact matrix is complete; provider profile and warm-pool gates remain open.
- [ ] Complete read-only partial recursion and matched quality/cost evidence.
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

- P1A.01 repository inventory: one head, `019fe0010001`, with linear ancestry
  through `019fdb010001`, `019fa2e4b7c1`, `019f8c1d2e3f`, `019f7950a1b2`
  and baseline `019f5b3c96bd`. No repository merge revision is indicated.
  Deployed database heads have not been inspected.
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
  repository gate. The current gate result is recorded above with 78.37%
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
  composite Session/Workspace lineage, and the Session status CHECK. Dirty-data
  preflight runs before DDL. SQLite upgrade, enforcement, downgrade and row
  preservation tests exist. The exclusive disposable PostgreSQL receipt now
  retains six contention scenarios and five bounded query-plan paths; deployed
  heads and representative deployed workloads remain unverified.
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
  and are the sole generic-pool-eligible profile. On 2026-09-07, the
  `.env`-resolved Daytona 0.210.0 operator lane created and checked immutable
  `fleet-rlm-python313-v7` (4 vCPU / 8 GiB / 8 GiB) and
  `fleet-rlm-python313-child-v2` (2 vCPU / 4 GiB / 4 GiB); both disposable
  no-Volume runtime probes verified their baked manifests, Python 3.13.13,
  non-root user, working directory and `git`, then confirmed probe Sandbox
  deletion. The prior v6/child-v1 identities remain immutable rollback targets.
  Volume mounts, pool reconciliation, backend capability checks and
  paid capacity remain unverified. The full doctor stopped earlier at the
  repository database/Alembic prerequisite; that is a separate unresolved
  live gate.
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
or authorize paid capacity.

### Implemented through Phase 6

- [x] Phase 0 vocabulary, ownership boundaries, evidence-lane distinctions, and the maintained plan/status ledger are in place.
- [x] Phase 1 additive Sandbox Binding lineage and Session status enforcement are covered by upgrade, dirty-data, downgrade, and preservation tests.
- [x] Phase 1.1 Daytona 0.210.0 integration, typed error/resource-race handling, benchmark comparison axes, and MLflow privacy/lifecycle mechanics are implemented and locally tested.
- [x] Phase 3 native-interpreter adapter/replay mechanics cover fresh contexts, native built-ins, typed submission, bounded output, authority checks, and retained cleanup behavior.
- [x] Phase 4 Session/SemanticChild/WorkspaceChild manifests, profile contracts, operator plan/check/create/verify commands, and immutable snapshot receipts are retained.
- [x] Phase 5 fresh per-Run native RLM/context/binding/worker mechanics are available through an explicit feasibility seam; the single selectable runtime remains `legacy`.
- [x] Phase 6 bounded capsules, selected-input/path validation, depth-one scheduling, shared reservations, ordered all-or-nothing batches, explicit read-only partial sibling outcomes, and typed child outcomes are implemented.
- [x] Repository-wide local validation passed: `make check` (exit 0), 78.37% backend coverage against a 75% threshold, generated-contract checks, 543 TUI tests, and documentation/boundary checks.

### Certification still open

- [x] PostgreSQL certification: the corrected exclusive campaign passed six contention scenarios and retained projected plans in `.fleet-evidence/receipts/adr006/postgres-contention-fleet_rlm_cert_5049b32b8ba0.json` (Alembic `019fe0010001`, PostgreSQL `170011`). The disposable target was removed after sealing.
- [ ] Live SDK/API-key, Volume, remote process containment, and stop/start or replacement continuity. The corrected Phase 3 receipt `.fleet-evidence/receipts/adr006/phase3-native-20260908T184525.json` still records a detached subprocess surviving context deletion; it is a provider-level native-production no-go, so `legacy` and the broker remain required. The durable Volume continuity receipt `.fleet-evidence/receipts/adr006/durable-continuity-20260908T1900.json` proves artifact readability/checksum across replacement, but does not prove native process containment.
- [ ] Native interpreter startup and capability checks on every production profile, including mounted WorkspaceChild behavior.
- [ ] Warm-pool eligibility, quota, clean-instance, lifecycle, demand, and cost evidence; paid capacity remains disabled.
- [ ] Complete MLflow exporter fault-injection, token-aggregation, configured-backend certification, and matched semantic/recursive quality-per-cost ablations. The local 3.16 receipt `.fleet-evidence/receipts/adr006/mlflow-local-certification-20260908T1858.json` is retained with unexercised cases visible; the failed MVP sample `.fleet-evidence/receipts/adr006/mvp-20260908T1905.json` keeps the quality gate open.
- [ ] Safe program loading, immutable promotion/rollback, native cutover, and subtraction of resident/broker migration machinery.

## Evidence and next tasks

Executable evidence lives in:

- `tests/unit/backend/test_binding_lineage_migration.py`
- `tests/unit/backend/test_turn_lineage_migration.py`
- `tests/unit/backend/test_database_compatibility.py`
- `tests/unit/backend/test_daytona_platform.py`
- `tests/unit/backend/daytona/test_native_sdk_contract.py`
- `tests/unit/backend/daytona/test_sdk_resource_errors.py`
- `tests/unit/backend/daytona/test_native_interpreter.py`
- `tests/unit/backend/daytona/test_run_environment_root_lease.py`
- `tests/unit/backend/test_host_tool_submit_broker.py`
- `tests/live/backend/test_phase3_daytona_native.py`
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

The final local validation passed `make check` (exit 0, 78.37% backend
coverage; the 75% threshold was met), including generated API and stream
checks, 543 TUI tests, boundary checks and documentation checks. The retained
live receipts are scoped evidence for their individual lanes; they do not
certify the pending architecture or authorize native selection.

Remaining sequence:

1. Retain deployed Alembic-head inventories for supported targets; the
   exclusive disposable PostgreSQL campaign and representative query plans are
   complete in the sealed receipt, while any additional deployment remains an
   operator-owned target.
2. Resolve the retained native no-go by certifying remote detached-process
   containment, then exercise fresh-context durable continuity and stop/start or
   replacement behavior before selecting native in policy. Until then, keep the
   proven broker route and its rollback machinery.
3. Reconcile every profile's cold/mount/capability behavior and warm capacity; do
   not infer paid warm capacity from image creation alone.
4. Complete MLflow backend/export-outage/concurrency certification and run matched
   recursive ablations with evidence-validity checks. Keep typed partial sibling
   publication behind a separately reviewed read-only policy.
5. Retain explicitly authorized live lanes before enabling cutover, immutable
   program promotion, deleting resident/broker rollback code, or activating
   broader child defaults.
