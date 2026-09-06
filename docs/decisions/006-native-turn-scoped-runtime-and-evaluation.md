# ADR 006: Native Turn-scoped runtime, recursive delegation, and MLflow evidence

Status: proposed; acceptance records the target architecture, not completed implementation or permission to enable it.

Date: 2026-09-06.

Source baseline: `main` at `bcb85cc7b29d625e4c399cbf0a56459d0617302e`.

Related decisions: [ADR 004](004-turn-interpreter-context.md) establishes fresh
Turn interpreter contexts and child data authority;
[ADR 005](005-runtime-variant.md) establishes one runtime selector. This ADR
complements those decisions with dependency targets, native execution adoption,
recursive evaluation, and MLflow ownership. The
[Session runtime ADR](ADR-session-scoped-rlm-state.md) continues to describe the
selected legacy implementation until the production cutover passes its gates.

## Context

Fleet already has native DSPy construction through `FleetProgramSpec` and
`RLMFactory`, `FleetToolCatalog`, `TurnBudget`, `DeadlineLMProxy`,
`FleetOutputContract`, an isolated DSPy compatibility module, a composition-owned
async bridge, scripted benchmarks, SQL claim/settlement contracts, and MLflow
tracing and quality tooling. These components must be certified and simplified
in place, not rebuilt under new names.

At the source baseline, Daytona is pinned to `0.207.0` and `runtime.variant`
selects `legacy`. Resident RLM registries, compatibility fingerprints, broker
execution, and automatic Session-context copying into recursive children remain.
Implementation presence is not live certification: scripted lifecycle results
cannot prove provider quality, remote process termination, or PostgreSQL
contention behavior.

The architectural problem is overlapping ownership of mutable execution state.
The goal is less lifecycle and transport code while preserving the
[existing architecture's](../../ARCHITECTURE.md) authorization, isolation,
publication, recovery, and public-client guarantees.

## Decision

Adopt a reusable Session sandbox with fresh execution state for every Run,
Daytona-native execution where equivalence is proven, bounded recursive children,
and MLflow as an evidence system rather than an execution controller.

### 1. Dependency and configuration contract

- Keep DSPy at `3.3.1`; upgrade the Daytona Python SDK to exactly `0.210.0` in an
  independently reversible change. Neither version is an open-ended latest pin.
- Keep the current MLflow 3 dependency policy. Certify the exact version resolved
  by `uv.lock`, its DSPy integration, and the configured tracking backend; an
  unrelated MLflow upgrade is not part of this decision.
- Keep model choices, budgets, environment profiles, and endpoints in resolved
  `config/fleet.toml` policy. Secrets come only from configured references.
- Preserve one `runtime.variant`. Expose `native-turn-scoped` only when its whole
  implementation exists. Any later capsule migration stage uses this same
  selector and has a removal gate; do not create orthogonal runtime/interpreter/
  recursion switches.

### 2. Durable state and execution ownership

| Concern | Owner |
| --- | --- |
| Run claims, cancellation, committed history, metadata, recovery obligations | Fleet SQL persistence; Alembic owns schema changes |
| Authorized durable workspace and artifact bytes | Workspace storage over Daytona Volumes |
| Session compute lookup, provisioning, reuse, retirement | One consolidated existing Session manager |
| RLM, interpreter context, tools, LM proxies, adapters, callbacks | One Run; fresh bindings per invocation |
| Successful result validation, publication and Turn Commit | Existing `RunLifecycle.finish()` and coordinator boundaries |
| User progress and terminal output | Existing Runtime Events, SSE and maintained TUI |
| Traces, assessments and experiment evidence | Existing MLflow integration and retained engineering receipts |

A Fleet Run is an execution attempt, not an MLflow tracking run. The current
user/assistant Turn-row encoding and public API are not changed by this ADR.

```text
Session: committed SQL state + authorized Volume files + reusable sandbox
  Run claim
    -> prepare authorized context and acquire Session sandbox
    -> create fresh interpreter context
    -> construct fresh native RLM and Run-local bindings
    -> execute under shared TurnBudget
    -> validate outputs and establish safe execution quiescence
    -> existing settlement and publication boundary
    -> context disposal / Session idle policy
```

Python variables persist across iterations within an invocation, not across
Turns. Process-scoped model templates remain immutable. A fresh namespace is
not OS, filesystem, or tenant isolation. Working directories and Volume subpaths
are not substitutes for verified authorization and path confinement.

Committed conversation and published artifacts remain distinct from immediate
workspace scratch and memory semantics. A failed Run does not automatically
roll back arbitrary Volume writes. No database transaction remains open across
Daytona or model-provider waits.

### 3. DSPy remains the program and loop owner

Retain `dspy.RLM`, its native `llm_query`, `llm_query_batched`, `print`, `SUBMIT`,
and native history/trajectory contract. Fleet's recursive tools remain distinct
extensions; a plain sub-LM call must not allocate another sandbox.

Reuse the existing program factory, tool catalog, budget, LM proxy and output
contract. Do not introduce a redundant wrapper Module, another agent loop, or a
second budget/proxy implementation. Unavoidable exact-version compatibility
stays in `compat_3_3_1.py`.

Acquire the remote interpreter asynchronously and pass the Fleet adapter through
DSPy's caller-owned interpreter contract. Fleet owns its cleanup. Concurrent
Runs never share an RLM instance or interpreter context.

Root actions, semantic calls, retries, repairs, child work, tools, and output
admission use the existing shared budget. Preserve root finalization capacity.
Admitted attempts, observed provider requests, cache hits, and known token usage
are different measurements. Closing admission does not stop already-running
work; the lifecycle owner must contain it separately.

### 4. Daytona replaces mechanisms only after parity

Keep SDK integration inside `src/fleet_rlm/daytona/`. Use typed SDK errors with
operation context: missing files, contexts, Volumes, and sandboxes have different
meanings. Re-test organization routing before removing private SDK workarounds.
Do not blindly retry ambiguous resource creation or multiply SDK upload retries.

Prefer native interpreter contexts, execution and output callbacks. Retain a
small host-tool gateway for bound DSPy callbacks and Fleet-authorized
capabilities. It validates Run authority, tool names, arguments, invocation IDs,
budgets and bounded results; it must not become another code execution server.
Its topology must work with a remote sandbox and the actual Fleet deployment,
including local-host deployments that cannot accept direct inbound calls.

Native adoption requires proof of interpreter/package selection, typed output,
output-byte limits, backpressure, nested host callbacks, cancellation and
subprocess containment. Client timeout or WebSocket closure is not proof of
remote termination. The exact 0.210.0 callback and output-accumulation behavior
must be tested. Use the proven broker path or a narrowly justified Toolbox
adapter when the public SDK cannot satisfy the required contract; do not weaken
bounds merely to remove code.

Use one application-owned async client per owning event loop. Keep each resource
owned until cleanup is confirmed or a durable, fenced recovery obligation
exists. Never reuse a sandbox with uncertain mutating work. Preserve cleanup
obligations after Session/Run deletion and prevent stale cleanup from deleting a
replacement generation. Ambiguous creation without a returned ID remains an
explicit reconciliation problem, not a fabricated durable resource identity.

### 5. Reproducible environments and optional warm capacity

Evolve the existing image builder into separate immutable image contents,
sandbox creation profiles, and capacity policy. Start with two images:
Session analysis and lean child analysis.

| Profile | Data access | Capacity policy |
| --- | --- | --- |
| SessionSandbox | Authorized Workspace Volume scope | Session prewarm, reuse and idle stop |
| SemanticChild | Bounded selected inputs, no Volume by default | Eligible clean warm capacity or cold fallback |
| WorkspaceChild | Selected files or verified restricted workspace access | Cold provision initially; reuse Session image unless measurements justify another |

Bake common evaluated dependencies into images, with reproducible dependency
resolution and a manifest for image/base/dependency/helper/resource identities.
Verify the actual native interpreter's executable, imports, user and working
directory. Expose bounded verified capabilities, not credentials or full
infrastructure details, to the model.

Use operator-owned plan/apply/check/doctor paths. Normal Turns do not build
images, activate snapshots or resize global pools. A host SDK upgrade alone
does not require a new snapshot. Preserve old immutable images while bindings
or pools still depend on them.

Distinguish Session-specific prewarm from Daytona's generic child warm pools.
Validate actual creation requests against the target SDK/backend requirements:
matching snapshot/region/default resources/default user, without disqualifying
creation-time envs, Volumes or secrets. Check organization support and quota;
keep cold operation correct. Delete used child sandboxes rather than returning
tenant data to clean shared capacity. Do not infer warm hits from latency alone.

Implement capacity policy in Phase 4; activate routine paid child capacity only
with Phase 6 demand/value evidence and explicit operator action. Keep idle-stop,
auto-delete and wall-clock lifetime limits distinct, with verified units/support.

The official Daytona Agent Skill may guide development, and MCP may support
operator inspection. Committed definitions plus SDK/API calls remain the
production source of truth; an interactive MCP transcript does not define the
environment.

### 6. Recursive RLM v2

Prefer deterministic Python, then a native semantic call or batch, then a Fleet
child RLM only for a subproblem that benefits from its own iterative exploration.
Do not add a compulsory planning-model call to choose among these levels.

Introduce one strict `SubproblemCapsule`: task, selected fragments, authorized
references, expected result shape, evidence requirements, and bounded allocation.
Cap serialized bytes, fragment/reference counts and result size. Replace
automatic full-Session copying; preserve the parent's committed-history interface
without reconstructing DSPy's native history. Documents, memory and child outputs
remain untrusted data.

Keep one native child depth and the bounded semantic fallback. Reuse the existing
executor, metrics, reservations and async bridge. Replace per-child private loops
with application-owned structured scheduling and bounded concurrency. Preserve
ordered all-or-nothing batches during that mechanical change; introduce typed
partial-result behavior separately.

Children return bounded evidence, status, source references, uncertainty and
usage. Root verifies, reconciles disagreement and remains the only final
publication authority. Partial evidence is allowed only for approved read-only
analysis after safe cleanup; authorization, isolation and cleanup failures remain
fatal. Cancellation must not be swallowed as an ordinary partial result.

Evaluate direct prediction, native RLM without Fleet recursive tools, existing
Fleet children, and capsule children on matched tasks. Hold context access,
models and total budgets constant initially. Measure evidence/correctness and
cost, not simply successful agent spawning. Include tasks that should avoid
recursion and record actual child LM invocations.

### 7. MLflow is a cross-phase evidence system

Reuse `MLflowRuntime`, tracing/callback owners, benchmark runners, quality
scripts and existing GEPA evidence. Preserve linked preparation/execution traces;
do not impose a new trace topology merely to obtain one root.

| Identity | Meaning |
| --- | --- |
| Fleet Session / Fleet Run | Durable conversation context / claimed execution attempt |
| MLflow trace / span | Instrumented execution phase / timed operation |
| MLflow tracking run | Benchmark campaign or optimization candidate record |
| Child call ID | Admitted recursive invocation linked to the parent Fleet Run |
| MLflow assessment | Scorer or human feedback; no settlement authority |
| MLflow artifact | Engineering evidence, not a user-facing Fleet Artifact |

Configure tracing once per application/worker lifecycle. Keep inference,
evaluation and compilation/optimization logging policies explicit and isolated;
do not change global MLflow or DSPy settings during active Turns.

Use existing DSPy autolog spans before adding manual LM/tool instrumentation.
Add manual observations only for Fleet-owned work not already represented.
Test parentage across the sync/async bridge, sequential Turns and concurrent
Sessions. Correlate model roles, child IDs, runtime/image/program identities,
settlement and cleanup outcomes. Avoid one span per SSE token or progress event.

Reconcile traces with budget/result accounting. Do not double-count nested token
totals or sum parallel child durations as parent wall time. Unknown usage/cost
is not zero. Sampling must not remove failures from benchmark denominators.
Separate Fleet settlement success from prediction/span success; no trace status
can authorize publication or prove remote containment.

Tracing is fail-soft for execution, while sensitive export fails closed. Test
actual exported inputs/outputs, exceptions, attributes, previews and artifacts
for secret/path sentinels and redaction failure. When safe export cannot be
established, suppress unsafe content/export rather than allowing raw fallback.
Never export credentials or hidden provider reasoning. Approved restricted
quality data is separate from content-free public engineering receipts.

Bound asynchronous export queues, retries and shutdown flush. Test outage,
expired credentials, saturation and slow export without stalling heartbeat,
cancellation or user execution. Trace IDs provide correlation, not access
control. Fleet and MLflow database/schema ownership remain separate.

Use one versioned dataset/scorer contract across DSPy evaluation and MLflow
assessment. Prefer scoring retained outputs over replaying mutations. Retain
failed, unscorable and missing-trace cases explicitly. Calibrate and version
judges; re-score baseline outputs when judges change. Managed datasets and
monitoring remain backend-capability checked and optional to local execution.

Keep the existing GEPA smoke non-promotable. Real candidates need isolated
execution, experiment budgets, feedback-rich metrics and held-out evidence.
Reuse prompt/program lineage tooling; prefer validated instruction/state
artifacts over serializing live clients, tools, credentials or locks. Certify
loading before serving or in an isolated process, because model loading may
restore global DSPy settings. Resolve approved versions at explicit startup or
release boundaries, never by hot-swapping an in-flight Run.

MLflow may block evidence-based promotion when evidence is unavailable; it may
not fail a committed user Turn or prevent safe rollback. It does not own Session
history, Run claims, sandbox cleanup, capacity policy or the TUI state machine.

## Phased adoption and required evidence

These are acceptance milestones, not declarations that work has run. Detailed
implementation tasks may evolve without changing the decision; changing an
ownership boundary or removing a gate requires a reviewed amendment. Preserve
existing implementations and distinguish existing, pending and certified status.

### Phase 0 - Architecture, vocabulary, and baseline contracts

Consolidate status and terminology around ADRs 004/005 and this decision. Record
source/dependency/configuration identities and event-fixture provenance. Define
Fleet versus MLflow identities and the three evidence lanes: scripted lifecycle,
adapter replay and live model/Daytona execution.

Gate: one coherent decision record and evidence vocabulary, without claiming
scripted results are live quality or infrastructure certification.

### Phase 1 - Database correctness and concurrency

Reconcile actual-main/deployed Alembic heads; preserve the existing composite
Turn/Run migration. Complete missing Sandbox Binding lineage and Session status
constraints with dirty-data preflight. Retain immediate SQLite FKs and narrow
claim-conflict reconciliation. Exercise PostgreSQL contention and recovery.
Observe claim/commit/recovery latency without exporting SQL values or credentials.

Gate: retained integrity/concurrency evidence; tracing does not participate in
transactions or settlement.

### Phase 1.1 - Foundation closeout, Daytona 0.210.0, and MLflow certification

Upgrade the SDK independently. Certify typed errors, organization routing,
resource lifecycle and relevant upload behavior. Extend the existing benchmark
comparison to permit only the intended experiment axis to differ. Capture the
legacy baseline on 0.210.0 before native promotion. Certify exact-lock MLflow
compatibility, parentage, redaction failure, bounded export and shutdown.

Gate: independent SDK rollback, meaningful comparison receipts and tracing that
cannot change execution or silently export unsafe content.

### Phase 2 - DSPy execution-core certification and simplification

Certify existing factory/tool/output/budget/proxy components end to end. Compare
stock/Fleet adapter behavior before deleting correction logic. Verify immutable
LM templates, protected finalization, usage attribution and tracing-on/off public
parity. No duplicate program wrapper, proxy, budget or loop is introduced.

Gate: native contracts and global accounting hold across root, semantic, child,
repair, retry, tool and output paths.

### Phase 3 - Daytona native-interpreter feasibility

Prove a caller-owned native vertical slice and the smallest viable host-tool
route. Exercise actual interpreter imports, typed output, nested callbacks,
output floods, slow consumers, cancellation, detached processes and late cleanup.
Compare broker/native causality and lifecycle timings in linked MLflow campaigns.

Gate: explicit go/no-go evidence for correctness, containment, output bounds,
latency and remaining compatibility; no broker deletion based on API existence.

### Phase 4 - Daytona environment definitions and warm capacity

Deliver two images, three logical profiles, preinstalled dependencies, verified
manifests and operator reconciliation. Implement Session prewarm and optional
child pool eligibility/cold fallback. Record build lineage, cold/restart/prewarm/
warm measurements and idle waste. Verify capabilities against 0.210.0 and the
actual backend, not rolling documentation alone.

Gate: reproducible environments and correct cold operation; pool implementation
is complete, but routine paid capacity waits for measured recursive value.

### Phase 5 - Native Turn-scoped production cutover and subtraction

Consolidate the existing Session compute owner, generation-aware binding and
fenced durable cleanup. Switch fresh RLM/context/bindings as one complete runtime
variant. Test restart/replacement continuity, claims, cancellation, artifacts and
public contracts. Retain existing settlement ordering and linked trace semantics.
Replace Workspace Agent operations only when SDK/Toolbox equivalents preserve
scope, symlink handling, size, checksum, locking and atomic publication guarantees.

After a bounded rollback gate, delete resident registries, fingerprints, rebinding,
lease transfers, duplicate root maps and broker execution mechanisms. Keep file
locks and resource fencing whose separate responsibility remains necessary.

Gate: durable continuity without cross-Turn Python state, safe containment and
actual ownership/code subtraction, with final settlement distinguished from
model and trace success.

### Phase 6 - Recursive RLM v2

Deliver bounded capsules, selected child data, shared budgets and depth-one
execution. Replace scheduling while preserving batch semantics; change typed
partial results separately. Connect eligible children to Phase 4 capacity.
Verify parent synthesis and evidence, including disagreement, unnecessary
recursion, repeated subproblems and oversized data.

Gate: matched MLflow ablations show when recursion improves quality per cost;
actual native child calls occurred, cleanup is contained, and paid capacity or
broader recursive defaults are enabled only with measured demand/value.

### Phase 7 - Evaluation, MLflow/GEPA optimization, rollout, and final deletion

Reuse datasets, scorers, alignment, annotations, monitoring and prompt tooling.
Share deterministic metrics between DSPy and MLflow; curate permitted data and
separate train/selection/held-out sets. Evaluate real GEPA candidates separately
from development smoke. Certify fresh-runtime loading and immutable approved
program versions. Retain campaign, judge, program, image and release lineage.

Roll out SDK, native runtime, capsules, warm capacity and optimized instructions
independently. Never shadow-replay arbitrary writes against the same live
workspace. Rehearse additive-schema rollback with active-Run draining/fencing.
Remove obsolete selectors, aliases, loops, instrumentation and unused snapshots
only after their replacement and retirement evidence exists.

Gate: one supported production path, calibrated quality/operational evidence and
reproducible release/rollback, without automatic policy mutation by monitoring.

## Alternatives considered

| Alternative | Reason not selected |
| --- | --- |
| Keep resident RLMs and arbitrary cross-Turn Python state | Preserves fingerprinting, rebinding and multiple state owners; durable continuity should be explicit |
| Delete broker/Workspace Agent immediately in favor of similarly named SDK methods | Native execution and file APIs do not automatically preserve bounds, containment, locks or atomic publication |
| One large snapshot and warm pool for all work | Couples dependency cost and capacity policy; Volume-backed Sessions and clean children have different requirements |
| Full Session copying, deeper native recursion or mandatory child fan-out | Increases context/cost without demonstrated quality gain |
| Independent runtime/interpreter/recursion flags | Creates an unnecessary compatibility matrix and permanent migration surface |
| MLflow as Run database, UI event source or automatic deployment controller | Duplicates Fleet authority and couples availability to observability |
| New orchestration framework or replacement evaluation stack | Adds abstractions where existing Fleet/DSPy/MLflow components already provide the necessary boundary |

## Consequences and limitations

Expected benefits are fewer mutable state owners, reproducible execution,
explicit persistence, smaller recursive inputs, and comparable quality/cost
measurements. They remain hypotheses until the named gates are exercised.

Costs include fresh context initialization, explicit rehydration, a bounded
period supporting rollback, and operator-run infrastructure/evaluation expense.
Hidden Python-state continuity is intentionally not preserved. Native SDK gaps
may justify retaining a small tested adapter. Warm pools remain optional, and
MLflow feature availability depends on the certified client/backend combination.

No fixed line-count reduction is a correctness gate. Every replacement must name
what is removed and which behavior-level tests preserve its obligations.

## Acceptance and scope

- [ ] Exact dependency targets are certified; existing DSPy/MLflow components are reused.
- [ ] Session continuity survives process, context and sandbox replacement using authorized durable state.
- [ ] Output bounds and remote containment are proven; uncertain mutation prevents reuse/publication.
- [ ] SQL claims, publication, recovery and generated Runtime Event/TUI contracts remain intact.
- [ ] Native built-ins remain native; children receive bounded selected data and cannot become final authority.
- [ ] Optional warm capacity has eligibility, quota, clean-instance and cost evidence.
- [ ] Trace parentage, privacy, non-duplicated usage and bounded export pass concurrency/failure tests.
- [ ] Live semantic/operational gates are distinct from scripted and non-promotable smoke evidence.
- [ ] Safe program loading, immutable promotion and rollback are demonstrated.
- [ ] Replaced resident/broker/migration machinery is deleted after its bounded rollback window.

This ADR is documentation only. It does not change dependencies, configuration,
schema, runtime behavior or infrastructure, and does not authorize paid live
validation, deployment, image/pool creation or prompt promotion. Those operations
require their own explicit requests and retained results.

For this documentation change, validate `make check-docs` and `git diff --check`.
Implementation phases use the scoped repository tests and authorized live lanes;
skipped or blocked validation must never be recorded as passed.

## References

Repository context:

- [Architecture and current ownership](../../ARCHITECTURE.md)
- [DSPy and Daytona integration](../how-to-guides/dspy-integration.md)
- [Snapshot workflow](../how-to-guides/daytona-snapshot.md)
- [Existing evaluation and monitoring tooling](../how-to-guides/evaluation-optimization.md)
- [Testing strategy](../how-to-guides/testing-strategy.md)
- [Reviewed source baseline](https://github.com/Qredence/fleet-rlm/tree/bcb85cc7b29d625e4c399cbf0a56459d0617302e)

External contracts (rolling documentation is guidance, not exact-version certification):

- [DSPy RLM API](https://dspy.ai/api/modules/RLM/)
- [Daytona 0.210.0 interpreter source](https://github.com/daytona/clients/blob/v0.210.0/sdk-python/src/daytona/_async/code_interpreter.py)
- [Daytona declarative builder](https://www.daytona.io/docs/en/declarative-builder/)
- [Daytona warm pools](https://www.daytona.io/docs/en/warm-pools/)
- [Daytona Agent Skill](https://www.daytona.io/docs/en/agent-skills/)
- [MLflow DSPy autolog and model loading](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.dspy.html)
- [MLflow evaluation API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.genai.html)
- [MLflow trace privacy](https://mlflow.org/docs/latest/genai/tracing/observe-with-traces/masking/)
