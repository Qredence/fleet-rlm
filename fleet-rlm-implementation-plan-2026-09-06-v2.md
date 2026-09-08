# Fleet RLM - Consolidated implementation plan

Revision: **2026-09-06, v2**; status refreshed **2026-09-07**. This document supersedes the A-G organization in the preceding plan and restores the original numbered phases, with MLflow work integrated throughout.

Reviewed source baseline: `main` at `bcb85cc7b29d625e4c399cbf0a56459d0617302e` (2026-09-06 18:46:13 UTC). This revision retains that audited baseline; it is not a claim that all of current main was re-audited or that pending tests were executed.

Required targets: **DSPy 3.3.1** and **Daytona Python SDK 0.210.0**. Keep the repository's current MLflow 3 dependency policy and certify the exact version resolved by `uv.lock`; do not introduce an unrelated MLflow upgrade without a demonstrated need.

Status vocabulary: **existing** means observed in source, **implemented** means the code and local evidence are present, **pending** means work or certification remains, and **certified** requires retained evidence on the supported live topology. An existing class, test, or script does not by itself close a live validation gate. Detailed checkboxes below now distinguish implemented mechanics from open certification work; the [ADR 006 implementation status ledger](docs/decisions/006-implementation-status.md) is the concise current-state receipt.

## 1. Objective and ownership

Make Fleet a maintainable, bounded recursive analysis system: DSPy supplies the RLM loop; Daytona supplies suitable execution primitives; Fleet owns authorization, budgets, selected context, durable settlement, and the public event contract; MLflow supplies engineering observability and evaluation evidence.

The target is a reusable Session sandbox with a fresh interpreter context, native RLM, tools, LM proxies, adapter state, and callbacks for every Run. A single invocation retains Python state across its RLM iterations, but cross-Turn correctness must not depend on resident Python globals.

| Concern | Authoritative owner |
| --- | --- |
| Run claims, cancellation, committed conversation, artifact metadata, recovery intentions | Fleet SQL persistence: supported SQLite/local policy and PostgreSQL deployment policy |
| Authorized durable workspace and artifact bytes | Workspace storage over Daytona Volumes |
| Sandbox lifecycle and native interpreter primitives | Daytona, mediated by the Fleet provider boundary |
| User-facing progress and terminal output | Existing Runtime Events -> SSE -> maintained TUI |
| Traces, benchmark records, assessments, optimization evidence | MLflow plus versioned engineering receipts |
| Runtime policy and approved program version | Committed configuration and explicit release/promotion process |

MLflow is not another source of truth for Session history, Run state, resource ownership, artifact publication, or cancellation. Trace data is not an RLM checkpoint.

Keep the native DSPy built-ins `llm_query`, `llm_query_batched`, `print`, and `SUBMIT`; Fleet's `rlm_query`/`rlm_query_batched` are separate extensions that create another iterative RLM execution. Do not create an extra sandbox for a plain semantic sub-LM call. RLM remains an experimental DSPy API, so the exact pinned compatibility tests remain important.

## 2. Existing implementation to preserve

| Existing work | Current files / symbols | Required treatment |
| --- | --- | --- |
| Native construction | `rlm/program.py`: `FleetProgramSpec`, `RLMFactory`, `build_program` | Certify and simplify in place, not another wrapper Module |
| Tool contracts | `FleetToolCatalog`, reserved-name validation | Reuse and keep capability boundaries explicit |
| Budget and retry boundary | `rlm/budget.py`, `DeadlineLMProxy` | Verify end-to-end coverage and calibrate; do not rebuild |
| Typed output binding | `rlm/output_contract.py` | Reuse for the native adapter |
| DSPy compatibility | `rlm/compat_3_3_1.py`, `rlm/submit_validation.py` | Keep only required, tested compatibility |
| Async host bridge | Existing `SyncBridgeDispatcher` | Reuse, not a loop per child |
| SQL integrity closeout | Immediate SQLite FKs and composite Turn/Run Session migration `019fdb010001` | Preserve; reconcile the remaining actual-main constraints |
| Benchmarks | `runtime_v2.py`, `adapter_replay.py`, existing live/latency scripts | Extend one evidence pipeline |
| Migration policy | Existing `runtime.variant` | Keep one selector; expose only implemented stages |
| MLflow lifecycle | `observability/mlflow.py`: `MLflowRuntime` | Reuse startup/shutdown and fail-soft state |
| Tracing and DSPy instrumentation | `observability/tracing.py`, `dspy_callbacks.py` | Preserve linked preparation/execution traces and avoid duplicate events |
| Quality tooling | Dataset, scorers, judges, alignment, annotations, prompt registry, monitoring scripts | Reuse and certify; these are not greenfield features |
| GEPA development evidence | `optimization/gepa_runner.py`, `mlflow_observability.py`, `evidence.py` | Preserve the explicit non-promotable smoke distinction |

The baseline still pins Daytona 0.207.0 and selects the legacy execution path. Resident RLM/fingerprint machinery and full Session-context copying to children remain. The current-main ORM still needs the missing Sandbox Binding lineage and Session status checks reconciled. The previous tracked roadmap reports live semantic-baseline and PostgreSQL-contention evidence as pending; do not infer their completion from scripted results.

The reviewed MLflow code already starts DSPy autologging, sanitizes and bounds trace values, supports local and configured Databricks destinations, and links preparation/execution traces. The revised work is to certify correctness, reduce overlap, tighten privacy/error behavior, and connect this existing tooling to the migration and recursive value gates.

## 3. Canonical phase sequence

| Phase | Scope | Starting status | MLflow deliverable |
| --- | --- | --- | --- |
| **Phase 0 - Architecture, vocabulary, and baseline contracts** | Retain ADRs, ownership, evidence definitions | Implemented; receipts linked | Identifier and evidence vocabulary |
| **Phase 1 - Database correctness and concurrency** | Remaining actual-main integrity and deployed-migration reconciliation | Additive SQLite implementation; deployed/PostgreSQL certification pending | Database/settlement timing with no SQL or secret payload leakage |
| **Phase 1.1 - Foundation closeout, Daytona 0.210.0, and MLflow certification** | SDK adoption, comparison axes, real baseline, tracing/privacy/lifecycle | SDK/MLflow mechanics implemented; live certification pending | Certified tracing and separate benchmark experiments |
| **Phase 2 - DSPy execution-core certification and simplification** | Reuse existing spec, budget, proxy, output contract, adapter | Existing owners preserved; end-to-end certification pending | Correct LM/tool/repair usage attribution |
| **Phase 3 - Daytona native-interpreter feasibility** | Prove interpreter, tool bridge, bounded output, remote cleanup | Adapter/replay mechanics implemented; native containment pending | Comparable native-versus-broker evidence |
| **Phase 4 - Daytona environment definitions and warm capacity** | Images, dependencies, profiles, manifests, prewarm and warm-pool management | Profiles/manifests/snapshot operator lane implemented; mounts/pools pending | Image lineage and cold/warm capacity measurements |
| **Phase 5 - Native Turn-scoped production cutover and subtraction** | Fresh Run resources, consolidated ownership, durable cleanup, SDK I/O parity | Feasibility mechanics are explicit-only; selection/cutover/subtraction pending | Correlated lifecycle, settlement and cleanup traces |
| **Phase 6 - Recursive RLM v2** | Capsules, child profiles, scheduler, typed results, recursive value proof | Capsule/scheduler/outcome mechanics implemented; quality/value proof pending | Parent/child lineage and quality-per-cost ablations |
| **Phase 7 - Evaluation, MLflow/GEPA optimization, rollout, and final deletion** | Shared scorers, datasets, feedback, program registry linkage, promotion | Existing scripts; integrate | Reproducible quality gates and approved release evidence |

Execution order: start the independent SDK upgrade in Phase 1.1A immediately. Phase 1 schema closeout, baseline tooling, and MLflow certification can proceed in parallel where diffs do not overlap. Phase 3 offline prototypes need the SDK and DSPy boundary, not completion of every operator-run live test. Phase 4 production definitions follow native feasibility, using a minimal disposable compatibility image for the spike if needed. Production cutover in Phase 5 requires the relevant live gates. Phase 4 owns warm-capacity implementation and canaries; routine paid child-capacity activation is gated by actual Phase 6 demand and quality evidence.

The old A-G plan is fully incorporated: A -> Phases 1/1.1/2; B -> 3; C and F1 -> 4; D and F2 -> 5; E -> 6; G -> 7. No older phase has been dropped or left as an implied appendix.

### Current todo state

- [x] Implementation surfaces through Recursive RLM v2 (Phase 6) are present and covered by local tests.
- [x] The Session and SemanticChild immutable snapshots are resolved from `.env`, checked against the Daytona contract, and have retained disposable runtime-probe receipts.
- [x] The final local repository gate passed (`make check`, including generated contracts, boundaries, docs, and 538 TUI tests).
- [ ] Deployed PostgreSQL/Alembic, native remote-containment, mounted-profile, warm-capacity, and matched semantic-quality gates remain open.
- [ ] Native cutover, safe program promotion/rollback, and resident/broker subtraction remain blocked on those gates.

See [ADR 006 implementation status](docs/decisions/006-implementation-status.md) for the detailed checked/open ledger and retained evidence paths.

## 4. Non-negotiable migration rules

1. Preserve claim ownership, cancellation, result validation, artifact/memory promotion, generated API contracts, and the maintained TUI.
2. Do not equate fresh interpreter namespaces with OS/filesystem/tenant isolation or the termination of all spawned processes.
3. Durable workspace writes are not automatically transactional; a failed Run does not undo arbitrary Volume writes.
4. A client timeout, closed WebSocket, or cancelled local task does not prove that remote execution stopped.
5. Keep one owner until cleanup is confirmed or a durable, fenced obligation exists. Never reuse compute with uncertain mutating work.
6. Keep one runtime migration selector. `legacy` remains the only selectable value until the complete Phase 5 containment and continuity path is certified; native feasibility uses explicit injected construction. Do not add orthogonal interpreter/recursion flags. Remove replaced stages after their bounded rollback window.
7. Tracing is fail-soft for execution, but sensitive content export must fail closed. A failed redaction callback must not silently allow raw data to leave the process.
8. Do not configure MLflow globally per Turn, child, tool, or concurrent evaluator. Runtime, benchmark, and optimization jobs have explicit process/lifecycle configuration.
9. Keep live tests, infrastructure mutations, deployment and repository writes explicit operator actions. A plan does not authorize them.
10. Every replacement names the old code to delete and behavior-level validation. A target line-count percentage is not an acceptance test.

## Phase 0 - Architecture, vocabulary, and baseline contracts

**Objective:** preserve the existing design decisions and make this numbered document the maintained execution plan, without reopening completed architecture work.

**Files:** `ARCHITECTURE.md`, ADR 004/005, the tracked scratch roadmap, benchmark receipts, this implementation plan.

- [x] **P0.01** Mark prior work as existing versus certified and link the actual evidence. The consolidated plan and implementation-status ledger supersede the old A-G/scratch sequencing without treating it as certification.
- [x] **P0.02** Keep ADR 004: reusable Session sandbox, fresh interpreter context per Run/Turn attempt, Volume-less SemanticChild, and restricted-data WorkspaceChild. Keep BenchmarkSandbox as a test profile rather than a product architecture.
- [x] **P0.03** Keep ADR 005 as the single runtime-selector policy, with a removal condition for each transitional stage.
- [x] **P0.04** Publish the identity glossary in Section 5 and record the reviewed commit, exact dependencies, configuration identity, and public-event fixture provenance before comparisons.

**Exit:** the existing foundation is preserved and terminology, implementation status, and evidence are unambiguous.

## Phase 1 - Database correctness and concurrency

**Objective:** complete the remaining guarantees on the actual reviewed main/deployed schema, not assume the earlier database branch was merged unchanged.

**Files:** `persistence/models.py`, `persistence/database.py`, `persistence/repositories/turns.py`, `sandbox_bindings.py`, `outbox.py`, migrations and database tests.

- [ ] **P1A.01** Inventory Alembic heads in current main and deployed databases, including any deployment of the earlier database-correctness branch. Select an additive/merge migration strategy where needed; never rewrite applied history.
- [x] **P1A.02** Keep the existing `fk_fleet_turns_run_session` migration and dirty-data preflight unchanged unless a reproduced issue requires a fix.
- [x] **P1A.03** Add the missing Sandbox Binding-to-Workspace and Binding-to-Session/Workspace lineage constraints in a new additive migration. Add the matching unique parent key only as necessary.
- [x] **P1A.04** Add a Session status CHECK and validate existing rows first. Define any binding-state CHECK around Fleet's normalized closed state model, not an assumed exhaustive list of provider wire values.
- [x] **P1A.05** Test upgrades from the actual preceding revision with valid data, orphaned bindings, cross-Workspace bindings, and invalid statuses. Reject dirty data without silently deleting or repairing it.
- [x] **P1A.06** Keep immediate SQLite FK checks and assert enforcement across connections; WAL/busy-timeout tuning remains optional local policy.
- [x] **P1A.07** Re-run expected-versus-unexpected claim-constraint tests. Preserve narrowly allowlisted conflict reconciliation and sanitized unknown failures.
- [ ] **P1A.08** Retain a real Postgres contention run for duplicate idempotency, conflicting input, active-Run exclusion, cancellation versus commit, stale recovery, and concurrent outbox ownership. A skipped lane is not evidence of passing.

- [x] **P1B.01** Keep Fleet persistence and the MLflow backend logically separate. Local SQLite files must be separate, and production deployments should use separate database/schema ownership and credentials. Fleet Alembic migrations must never manage MLflow tables.
- [ ] **P1B.02** Measure query count and query plans for Session listing, ordered history, claim reconciliation, recovery scans, and outbox claims. Remove redundant indexes or queries only after a demonstrated overlap and SQLite/PostgreSQL plan tests; do not normalize every JSON payload or introduce a generic repository framework.
- [ ] **P1B.03** Keep database transactions short and resource/API calls outside them. Preserve current atomic result/Turn/artifact metadata commit and independent immediate workspace-file semantics.
- [ ] **P1B.04** Add bounded timing and failure-category observations for claim, commit, recovery, and outbox work. Do not record raw SQL parameters, database URLs, private storage references, or credentials. Fleet state remains authoritative even when no trace is exported.

**Exit:** missing lineage is database-enforced, deployed migration history is respected, concurrency tests retain evidence, and observability does not participate in settlement.

## Phase 1.1 - Foundation closeout, Daytona 0.210.0, and MLflow certification

### 1.1A. Exact SDK upgrade

**Files:** `pyproject.toml`, `uv.lock`, `daytona/platform.py`, `daytona/errors.py`, packaging and provider tests.

- [x] **P1.1A.01** Pin `daytona==0.210.0`; refresh the lock narrowly and inspect transitive changes. Keep DSPy at `3.3.1`.
- [x] **P1.1A.02** Add local dependency/import checks for the supported Python versions and record installed SDK/API-client identities in benchmark/engineering evidence; do not add a user configuration knob for them.
- [x] **P1.1A.03** Prefer typed Daytona errors and structured status/code/source at the adapter boundary. Keep Fleet's sanitized public error taxonomy.
- [x] **P1.1A.04** Preserve operation context when classifying absence. A missing file, process, interpreter context, Volume, and sandbox are different outcomes. Never recreate a sandbox because an unrelated operation returned 404.
- [x] **P1.1A.05** Handle transient rate limits separately from persistent capacity/quota rejection. Respect documented retry metadata only within the existing absolute deadline and attempt limit. Do not blindly retry creation after an ambiguous network outcome.
- [x] **P1.1A.06** Cover Volume get/create races, typed not-found, authentication, authorization, rate-limit, conflict, transport failure, and 5xx outcomes. Creation must occur only for explicit missing Volume, not for arbitrary errors.
- [x] **P1.1A.07** Re-test API-key organization routing. The installed 0.210.0 behavior still uses one narrow, tested compatibility function because the public path does not preserve scope on its own.
- [x] **P1.1A.08** Normalize create/start/stop/delete and Volume errors consistently. Preserve readiness verification and cleanup confirmation until SDK behavior replaces them demonstrably.
- [ ] **P1.1A.09** Exercise SDK snapshot build-context upload behavior without adding a second retry loop around the SDK's S3 uploader. Keep this distinct from `sandbox.fs.upload_file` and ordinary artifact I/O.
- [ ] **P1.1A.10** Keep the current snapshot for this PR. A host SDK upgrade alone does not change the installed sandbox packages or require a new image.
- [x] **P1.1A.11** Record MI355X support as reviewed but out of scope. Do not introduce GPU configuration for CPU-only workloads.
- [ ] **P1.1A.12** Record a compatibility receipt covering unit tests and, separately when run, live Volume, sandbox, broker, upload, lifecycle, and public-event smoke tests.

### 1.1B. Reproducible benchmark evidence

**Files:** `scripts/benchmarks/runtime_v2.py`, `adapter_replay.py`, `run_rlm_latency.py`, lifecycle/live benchmark scripts, existing scorer helpers.

- [x] **P1.1B.05** Fix benchmark comparison axes. `runtime_v2.compare()` now accepts one explicit runtime, SDK, or snapshot axis to differ while rejecting unrelated model/dataset/scorer/policy drift.
- [x] **P1.1B.06** Keep three evidence lanes distinct: scripted HTTP lifecycle, adapter replay, and live model/Daytona execution. Preserve the explicit `not_exercised` state.
- [ ] **P1.1B.07** Add live executable scenarios for retrieval from large context, deterministic verification, file/artifact work, native sub-LM batching, recursive exploration, and failure/cleanup behavior. Score actual outputs, not merely keywords echoed by a scripted backend.
- [ ] **P1.1B.08** Record per-scenario samples, failures, context bytes, admitted calls, observed tokens, sandbox lifecycle times, and source/model/image digests. Stratify cold/warm and cache modes. Do not use five scripted samples as a reliable infrastructure p95 claim.
- [ ] **P1.1B.09** Capture an authorized legacy baseline on 0.210.0 before native cutover; retain an SDK-only 0.207.0 comparison separately when required. Store receipts in a durable engineering artifact location, not solely local `.scratch` paths.
- [ ] **P1.1B.10** Review current `max_provider_attempts=2048` as a safety ceiling, not a tuned efficiency policy. Calibrate from measured tasks; do not invent a tighter number before evidence exists.
- [x] **P1.1B.11** Consolidate the checked-in scratch roadmap and ADR status into one maintained implementation plan with links to evidence. Keep implemented and certified statuses separate.

- [ ] **P1.1B-M.01** Record each benchmark campaign as an MLflow tracking run, with explicit purpose and configuration identity. Log sealed receipts and bounded non-content sample summaries; content-bearing evaluation datasets stay in the approved restricted store.
- [ ] **P1.1B-M.02** Keep exact source/config/model/image/dataset/scorer identities as parameters/tags and measured numeric outcomes as metrics. Preserve missing values as unknown rather than zero.
- [ ] **P1.1B-M.03** Capture full-run measurements independently of trace sampling. An unavailable or sampled-out trace is not a zero-cost task and must not disappear from failure denominators.
- [ ] **P1.1B-M.04** Mark campaigns with evidence lane, exercised gates, and promotion eligibility. Scripted echo/keyword receipts and development GEPA smoke cannot satisfy a live semantic-quality gate.

### 1.1C. Certify the existing MLflow integration

**Files:** `observability/mlflow.py`, `observability/tracing.py`, `observability/dspy_callbacks.py`, config resolution, `scripts/validate_mlflow_tracing.py`, existing MLflow tests.

The existing implementation already handles lifecycle startup/shutdown, DSPy autologging and linked preparation/execution traces. Extend this owner rather than introducing a second instrumentation service.

- [ ] **P1.1C.01** Certify the exact lock-resolved MLflow version against DSPy 3.3.1, DeadlineLMProxy, async RLM invocation, recursive host callbacks, and the configured local/Databricks backend. Record SDK and tracking-backend capability provenance separately.
- [x] **P1.1C.02** Keep tracking destination, experiments, content policy, sampling, and queue settings under resolved Fleet configuration. Configure once before serving; preserve local development and explicitly configured Databricks/Unity Catalog lanes without silently switching destinations.
- [x] **P1.1C.03** Make runtime autolog behavior explicit: inference traces enabled when permitted; compilation/evaluation logging and their traces disabled for serving. Configure evaluation/optimization jobs separately, not by changing global autolog options during active Turns.
- [x] **P1.1C.04** Preserve current preparation/execution trace linkage and existing optional public trace ID. Add Fleet Run/Session correlation as necessary; do not force a topology rewrite merely to make a single root span.
- [x] **P1.1C.05** Establish and locally test a capture/export contract: bounded operational metadata by default, approved sanitized content only when needed for restricted evaluation/debugging, and no provider hidden reasoning, secrets, system-prompt dumps, unbounded history, file bytes, or private infrastructure paths.
- [x] **P1.1C.06** Test redaction failures as well as successful redaction. If safe export cannot be established, disable content capture/export or emit a safe replacement using verified supported mechanisms; do not assume throwing from a processor prevents export. Keep execution unaffected.
- [x] **P1.1C.07** Audit exporter-visible surfaces covered by the installed SDK, including automatically captured inputs/outputs, exception events, attributes, tags, trace previews, logs, and serialized model artifacts. Test known secret/path sentinels on exported payloads, not just in-memory helper output.
- [x] **P1.1C.08** Apply explicit, bounded asynchronous trace export, worker/queue limits, retry lifetime, and shutdown flush. Verify the trace-export controls independently from ordinary metric/parameter async logging; do not rely on shifting SDK defaults.
- [ ] **P1.1C.09** Inject exporter outage, expired credentials, queue saturation, sampler changes, slow backend, and shutdown flush stalls. Verify claim heartbeats, cancellation and Turn latency remain within their budgets; record dropped/export-failed evidence rather than blocking useful work.
- [ ] **P1.1C.10** Test repeated application lifespans, process-global configuration, and context cleanup. Prevent one Session or separate evaluation job from contaminating another trace. Run evaluation/optimization in separate worker processes when global SDK settings require isolation.
- [ ] **P1.1C.11** Define experiment purpose separation for runtime, evaluation, and optimization. Use existing names plus purpose tags where sufficient; do not recreate managed experiments or migrate Unity Catalog trace locations without explicit operator action.
- [ ] **P1.1C.12** Validate that authenticated trace lookup remains separate from public SSE/TUI access. A trace ID is correlation, not authorization; clients must remain functional when tracing is disabled or a trace is unavailable.

**Exit:** Daytona 0.210.0 is independently certified; a real legacy baseline is retained before promotion; MLflow failures do not change execution and unsafe data cannot be exported silently.

## Phase 2 - DSPy execution-core certification and simplification

**Objective:** finish certification and reduce overlap in the already implemented DSPy boundary. Do not reimplement Phase 2 from the earlier roadmap.

**Files:** `rlm/program.py`, `budget.py`, `compat_3_3_1.py`, `output_contract.py`, `submit_validation.py`, existing construction, adapter, budget and callback tests.

- [ ] **P2A.01** Trace budget ownership through root actions, extraction, native sub-LM calls, schema fallback, parse repairs, provider retries, recursive children, host tools, and output admission. Confirm exactly one intended debit per admission path.
- [ ] **P2A.02** Preserve the current distinction between admitted attempts and observed network requests: cache hits can consume admission without an outbound call. Do not call token estimates an exact hard limit.
- [ ] **P2A.03** Verify root finalization reserve is not usable by children, post-settlement admissions fail, and closing admission does not substitute for cancelling already running work.
- [ ] **P2A.04** Test provider-template immutability, proxy copying, callback/usage accounting, schema capabilities, and sync/async parity. Add only missing regressions; do not implement a second LM proxy.

- [ ] **P2B.01** Keep FleetProgramSpec/FleetToolCatalog/RLMFactory as one construction seam. Validate custom-tool names against the built-ins at final construction, and preserve the bound native callbacks required by the remote interpreter.
- [ ] **P2B.02** Compare stock JSONAdapter and the Fleet adapter on fixed real failure cases. Remove only measured redundant correction paths; retain necessary typed-SUBMIT validation and extraction behavior. Do not add an outer agent loop that duplicates native RLM iteration.
- [ ] **P2B.03** Keep unavoidable DSPy 3.3.1 private integration in compat_3_3_1.py. Remove obsolete private mutation and legacy aliases only after all callers migrate and contract tests pass.
- [ ] **P2B.04** Keep native REPLHistory within one invocation and committed Session history as distinct data. Do not independently reconstruct or compact DSPy native history.
- [ ] **P2B.05** Remove hardcoded model selection from application helpers when the selected runtime policy already owns it, without mixing model changes into interpreter migration comparisons.

- [ ] **P2M.01** Test DSPy autolog plus Fleet callbacks plus DeadlineLMProxy for duplicate LM/tool spans and duplicate usage accumulation. Prefer native autolog spans; add Fleet-owned spans or attributes only for work not already represented.
- [ ] **P2M.02** Attach an explicit model role and execution identity to root, sub-LM, child-root, child-sub-LM, repair, finalization, and optimization-reflection work where applicable. Do not attribute all nested calls to the root.
- [ ] **P2M.03** Reconcile tracing observations with the authoritative TurnBudget/result metrics: admissions, physical attempts when observable, cache hits, known tokens, and unknown usage remain different quantities.
- [ ] **P2M.04** Keep Runtime Event projection independent from MLflow. Do not emit one span per SSE/progress token or re-read traces to drive the UI. Add tracing-on/off output and event-contract parity tests.

**Exit:** one program/adapter/budget boundary is certified, native built-ins remain native, call attribution is accurate, and no new observability or execution owner duplicates existing code.

## Phase 3 - Daytona native-interpreter feasibility

**Objective:** prove the exact DSPy/Daytona path before production snapshot and runtime cutover decisions.

### 3A. Native vertical slice

**Files:** existing interpreter adapter, a small `daytona/native_interpreter.py` only if separation helps, output contract, environment composition and compatibility tests.

- [x] **P3A.01** Implement DSPy's existing CodeInterpreter contract against `sandbox.code_interpreter`, not a second generic interpreter interface.
- [x] **P3A.02** Acquire the sandbox asynchronously, create an explicit context, and pass the Fleet adapter as a caller-owned interpreter to a fresh RLM invocation. Fleet owns closure; do not force async acquisition into a synchronous factory.
- [x] **P3A.03** Reuse `FleetOutputContract`, existing serializable attachment/history transport, result validators, and public Runtime Event projection.
- [ ] **P3A.04** Verify the actual native interpreter's Python executable, version, installed-package path, user, and working directory on the current snapshot. An image having Python 3.13 installed does not prove the provider interpreter selected it. If that snapshot cannot run the native interpreter, use a minimal disposable compatibility image for this proof; defer the final production image/profile split to Phase 4.
- [x] **P3A.05** Replay tests prove variables/imports/functions survive iterations within one explicit context and are absent in another. Live provider containment remains a separate gate; Daytona's shared default context is not used.
- [x] **P3A.06** Replay tests prove native `llm_query`, batched queries, a Fleet host tool, and typed `SUBMIT` work. DSPy's functions remain native and their bound callbacks are transported rather than recreated.
- [x] **P3A.07** Keep private/native type detection, if still unavoidable under 3.3.1, in the existing compatibility module. No new per-version framework.

### 3B. Host-tool transport, not a second execution server

- [ ] **P3B.01** Reuse the composition-owned bridge. Prove the nested path: root code waits for host callback, callback schedules a child, child completes, root resumes, cancellation still works.
- [ ] **P3B.02** Choose transport consistent with Fleet's deployed topology. Do not assume a sandbox can directly reach a local Fleet host; retain a small polling/proxy bridge if necessary.
- [x] **P3B.03** Restrict the gateway to invocation ID, Run-scoped authorization, bound tool name, arguments, and bounded results. Keep provider credentials and database credentials out of sandboxes.
- [x] **P3B.04** Validate authority and schema on every call and before publishing results. Prevent replay across Runs and reject late calls after revocation.
- [ ] **P3B.05** Deduplicate retryable tool requests where safe. Do not describe arbitrary external writes as exactly-once merely because a request ID exists.
- [x] **P3B.06** Preserve native built-in tool identity while supporting host transport. Reserved-name validation applies to Fleet custom tools, not to the bound native callbacks required by the interpreter.
- [x] **P3B.07** Ensure ordinary stdout cannot be confused with a final-output control message. Validate schema and keep final output distinct from durable settlement authority.

### 3C. Output, cancellation and cleanup limits

The previously inspected exact Daytona 0.210.0 implementation invokes output handlers synchronously and accumulates stdout/stderr into its result. The public context APIs document client wait timeouts separately from server cancellation. Verify these exact-version behaviors in the compatibility tests rather than treating streaming or timeout arguments as proof of safety.

- [x] **P3C.01** Use synchronous, fast output callbacks that feed a bounded event bridge. Do not pass un-awaited async callbacks to this SDK version.
- [x] **P3C.02** Enforce byte bounds before output accumulates indefinitely. Replay tests cover output overflow, bounded event delivery and slow-consumer behavior; remote SDK accumulation/containment remains a live gate.
- [x] **P3C.03** Apply an absolute host deadline and a bounded provider execution timeout. Include connection establishment, tool waits, execution, and cleanup; never use an unbounded timeout accidentally.
- [ ] **P3C.04** Test cancellation during context creation, execution, native sub-LM calls, host callbacks, and finalization. Track late-created contexts/sandboxes and do not blindly create replacements.
- [ ] **P3C.05** Probe long-running and detached subprocesses after cancellation and context deletion. When process termination is uncertain, stop/delete and quarantine the Session sandbox before reuse.
- [x] **P3C.06** Preserve no-success/no-publication behavior after authority loss. Test no delayed mutation after a subsequent Run begins.
- [ ] **P3C.07** Use separate sandboxes for tenant/Session security isolation. Test same-Session overlapping Runs are rejected; different Sessions can execute concurrently without shared bindings.
- [x] **P3C.08** Compare broker/native protocol outputs and event causality. For parallel events, compare allowed partial order rather than demanding identical scheduling order.
- [ ] **P3C.09** Record a go/no-go receipt: correctness, output bounds, timeout/cancellation, cleanup, latency, and complexity. Record any retained compatibility code and why it remains necessary.

- [ ] **P3M.01** Measure acquisition, context creation, bootstrap, first action, subsequent action, host-tool round trip, cancellation containment, and cleanup separately. Compare native versus broker under the same task/model/image settings.
- [ ] **P3M.02** Use existing Fleet lifecycle spans for timings and DSPy spans for LM/tool work. Represent an SDK operation once, even if lower-level transport instrumentation is also enabled.
- [ ] **P3M.03** Propagate context explicitly across the existing sync/async bridge and child scheduling boundary; verify parent IDs under concurrent Sessions. Keep instrumentation credentials on the host.
- [ ] **P3M.04** Attach a go/no-go receipt and capability results to the campaign MLflow run. A missing/failed trace cannot stand in for a passing output-bound, process-containment or cleanup test.

**Exit:** native execution meets the actual output, host-tool, deadline, remote-containment and public-contract requirements. Otherwise keep the proven broker path or one justified narrow Toolbox adapter, rather than deleting essential guarantees.

## Phase 4 - Daytona environment definitions and warm capacity

**Objective:** define the complete environment system, including preinstalled dependencies and warm capacity, while separating implementation from paid capacity activation.

Two images are sufficient initially: Session analysis and lean child analysis. The logical profiles are SessionSandbox, Volume-less SemanticChild, and restricted-data WorkspaceChild; WorkspaceChild can reuse the Session image until measured dependencies justify another image. Image names become immutable content/version identities, not aliases overwritten in place.

### 4A. Images, profiles and manifests

**Files:** `daytona/provisioning.py`, `snapshot_contract.py`, requirements/package assets, `scripts/daytona_snapshot.py`, config policy.

- [x] **P4A.01** Separate immutable image contents, sandbox creation profile, and optional warm-capacity policy within the existing Daytona package. Evolve `DaytonaSandboxSpec`; avoid duplicate representations of the same fields.
- [x] **P4A.02** Support two image profiles: Session analysis and lean isolated-child analysis. Keep benchmark environments as uses of these profiles, not a third product abstraction.
- [x] **P4A.03** Preserve the pinned base image and working package set initially. Resource sizes are explicit per-profile contracts and remain within the provider limit; future changes require retained capacity evidence.
- [x] **P4A.04** Make Python dependencies reproducible, including transitive resolution/hashes where feasible. Bake common packages during image build, not each Turn.
- [ ] **P4A.05** Add dependencies only for declared supported tasks: for example spreadsheet or PDF parsing when those workflows are evaluated. Add missing-import telemetry for future decisions; do not build an indiscriminate scientific image.
- [x] **P4A.06** Add a runtime manifest: schema version, image definition digest, base digest, Python executable/version, dependency digest, helper protocol, declared capabilities, and default resources.
- [x] **P4A.07** Verify the manifest plus a small executable/import probe on a new sandbox generation. The retained probes are no-Volume evidence scoped to the verified identity; a manifest is metadata, not proof that arbitrary generated code is authorized.
- [x] **P4A.08** Expose bounded capability metadata to the RLM so it knows which packages/tools exist. Do not inject credentials, full dependency dumps, or infrastructure identifiers into prompts.
- [ ] **P4A.09** Verify native interpreter startup on each built profile. Add only the small helper/client actually required by Phase 3; never bake Fleet backend, DSPy host orchestration, DB code, or credentials into the image.
- [x] **P4A.10** Publish new immutable snapshot names only when contents/resources change. The `.env`-resolved `fleet-rlm-python313-v7` and `fleet-rlm-python313-child-v2` identities are retained while active bindings may reference them; v6/child-v1 remain rollback targets.

### 4B. SDK/API-driven operator reconciliation

- [x] **P4B.01** Reuse the current snapshot script and CLI composition. Add non-mutating plan/check behavior and an explicit operator apply path; do not create parallel provisioning commands with different rules.
- [x] **P4B.02** Use Daytona's public Image/Snapshot SDK surfaces for creation and inspection. Keep SDK/API version assumptions explicit; rolling documentation is not proof a feature is available in the deployed backend.
- [x] **P4B.03** Make checks fail on definition drift rather than overwriting existing snapshot identities. Treat activation, retirement, and warm-pool changes as explicit operator actions.
- [ ] **P4B.04** Extend `fleet doctor daytona` with actual snapshot, region, runtime-manifest, interpreter, mount, and optional capacity readiness. Report unavailable features without pretending they were exercised.
- [ ] **P4B.05** Implement and test warm-pool plan/check/reconcile behavior in this phase, with an explicitly authorized canary where available. Keep routine paid capacity disabled until eligibility, containment and actual Phase 6 demand/value are certified. Image correctness must not depend on warm capacity.
- [x] **P4B.06** Document the official Daytona Skill as implementation assistance and optional MCP as developer/operator tooling. Runtime/provisioning source of truth remains committed definitions plus SDK/API calls, not an interactive MCP transcript.
- [ ] **P4B.07** Test packaging so installed Fleet distributions contain every dependency manifest and required helper asset.

### 4C. Session prewarm and recursive-child warm pools

Daytona documents warm-pool matching on snapshot, region, default resources and the default OS user, without custom creation-time envs, Volumes or secrets. It also requires organization enablement and quota. Verify both SDK 0.210.0 and deployed-backend support. Baked non-secret image configuration is not the same as per-create custom environment values.

- [x] **P4C.01** Reuse existing Session prewarm in the consolidated manager. Trigger it only for a bounded high-intent action such as new Session/attachment preparation, not every UI interaction.
- [x] **P4C.02** Give active Runs priority over prewarm; bound prewarm concurrency and idle retention. Measurement of useful hits and wasted sandbox seconds remains a live evidence task.
- [x] **P4C.03** Keep Volume-backed Session sandboxes on prewarm/start/reuse, not provider warm pools. Default to idle stop when no runtime-memory persistence is required.
- [ ] **P4C.04** Implement the child warm-pool policy and its eligibility/cold-fallback path now. Run an explicitly authorized capacity canary only after confirming organization support; routine activation waits for measured Phase 6 recursive demand.
- [ ] **P4C.05** Validate the actual SDK creation request for eligibility. Avoid accidentally passing disqualifying volumes, custom envs, secrets, users, or resource overrides.
- [ ] **P4C.06** Reconcile desired capacity through the operator path. Never resize/create global warm pools from an individual user Turn.
- [ ] **P4C.07** Delete used child sandboxes; do not return tenant-used contexts/files to a shared warm pool. Record cleanup separately from pool replenishment.
- [ ] **P4C.08** Record observable claim/readiness metadata accurately. Since claimed warm identifiers may be cleared, do not infer a warm hit solely from a fast create time.
- [ ] **P4C.09** Specify lifecycle units explicitly. Idle auto-delete and wall-clock TTL are not interchangeable; use only parameters supported by 0.210.0 and the deployed backend, and verify expiry behavior.
- [ ] **P4C.10** Evaluate pool sizes against demand, quota, cold-start latency, idle spend, and parent admission capacity. Do not hard-code a pool of three without workload evidence.

- [ ] **P4M.01** Log immutable image-definition, base-image, dependency-lock, helper-protocol and resource-profile digests with build/check receipts. Keep internal infrastructure identifiers private; use opaque engineering correlation where needed.
- [ ] **P4M.02** Compare cold creation, stopped-Session restart, useful Session prewarm, and eligible child warm acquisition as distinct experiments. Record ready time, first-action time, idle waste, quota rejection and fallback counts.
- [ ] **P4M.03** Report warm-hit status as unknown when the provider does not expose evidence. Do not infer a hit from low latency or a cleared warm-pool identifier.
- [ ] **P4M.04** Keep warm-pool reconciliation outside user Turns and outside the RLM tool catalog. MLflow stores observations; it does not autonomously create pools or change desired capacity.

**Exit:** reproducible profiles, preinstalled dependencies, verified manifests, operator reconciliation, Session prewarm and optional child warm-capacity implementation all exist. Cold operation remains correct, and production warm activation is an explicit evidence-backed action.

## Phase 5 - Native Turn-scoped production cutover and subtraction

**Objective:** replace resident RLM/interpreter reuse with a complete Run-local execution bundle while retaining durable Session continuity.

### 5A. One Session compute owner and durable cleanup

**Files:** existing `daytona/session_manager.py`, `daytona/runtime.py`, `runtime/daytona/run_environment.py`, `composition/live.py`, binding/recovery persistence.

- [x] **P5A.01** Make the existing Session manager the single owner of lookup/create/start/reuse/replace/idle-stop. Do not add a second SessionSandboxManager while keeping all existing owners.
- [x] **P5A.02** Separate Session sandbox ownership from Run-owned interpreter context/tool/callback resources. Keep one database Run claim as cross-process execution authority.
- [x] **P5A.03** Add only necessary binding metadata: expected snapshot/profile identity and a monotonic ownership generation or equivalent fencing identity. This is provider-resource fencing, not a replacement program fingerprint.
- [x] **P5A.04** Use short transactions and compare-and-set binding updates. Never hold a database transaction across a Daytona create/start/network wait.
- [x] **P5A.05** Resolve prewarm versus active-Run races across processes. Losing/late provisioning attempts must not replace a valid binding and must remain owned for cleanup.
- [x] **P5A.06** Use one application-lifespan AsyncDaytona client per owning event loop. Close it after dependent tasks/resources drain. Do not reuse an async client on arbitrary recovery threads or fresh loops.
- [x] **P5A.07** Use the existing durable cleanup/recovery intents only for obligations not represented elsewhere. Store exact resource/context identity, expected generation, action, retry state, and sanitized failure category.
- [x] **P5A.08** Keep cleanup obligations alive after Session/Run deletion; avoid cascading away the only record of an external resource. Cleanup intent ownership must not grant authority to publish outputs.
- [x] **P5A.09** For ambiguous creation with no returned ID, retain bounded in-flight ownership and reconcile with an operation identity/labels where supported. Document the remaining discoverability limit; a database intent cannot invent an unknown sandbox ID.
- [ ] **P5A.10** Test crash windows around create, bind, context startup, commit, delete, and cleanup acknowledgment. Repeated recovery must not delete a replacement or duplicate committed output.

### 5B. Complete Turn-scoped execution path

- [x] **P5B.01** Retain the native RLM/context/binding mechanics as explicit feasibility construction. The existing runtime selector exposes only `legacy` until cutover evidence passes.
- [x] **P5B.02** Build a fresh native RLM through the existing `RLMFactory` per Run, with the existing shared TurnBudget and fresh LM proxies, tools, output contract, and callbacks.
- [x] **P5B.03** Acquire a fresh explicit interpreter context for that Run. Use the existing authorized attachment/history/memory projections rather than reconstructing DSPy REPLHistory.
- [x] **P5B.04** Preserve within-Turn iteration state. End cross-Turn Python globals, tool aliases, callbacks, model configuration, and output-schema reuse.
- [ ] **P5B.05** Keep durable file/history continuity. Test Turn A writes a durable file; Turn B receives a new context and reads the file while Turn A's Python-only variable is absent.
- [ ] **P5B.06** Test stop/start and full sandbox replacement with the same authorized Volume scope. Verify history, file checksums, memory metadata, and artifact lookup still work.
- [x] **P5B.07** Classify mutable scratch versus promoted immutable artifacts. Preserve existing promotion/locking/checksum behavior; do not claim failed Runs roll back arbitrary filesystem writes.
- [ ] **P5B.08** Integrate quiescence and cleanup into the existing settlement owner/order. Revoke useful-work capability, drain child/tool work, validate outputs, close owned context, and commit only when the established success obligations are satisfied.
- [x] **P5B.09** Verify generated HTTP/settings/stream contracts and the maintained TUI. Same-Session overlap remains forbidden; cross-Session concurrency remains available.

### 5C. Explicit resident-runtime and broker subtraction

- [ ] **P5C.01** Remove resident `SessionRLMRegistry` from native production composition and then remove its implementation after rollback/cutover evidence is retained.
- [ ] **P5C.02** Remove resident program fingerprints, RLM-generation rotation, stable tool proxies, LM rebinding, observer rebinding, and interpreter-transfer logic.
- [ ] **P5C.03** Remove duplicate Session root maps. Keep resource generation/fencing and workspace-file locks where their distinct responsibility remains necessary.
- [ ] **P5C.04** Contract the broker to proven host-tool mediation. Delete custom code execution, REPL namespace ownership, and result/output polling that the native adapter replaces.
- [ ] **P5C.05** Remove private loops and process-global cleanup collections only when their obligations have another proven owner. Do not replace them with new collections elsewhere.
- [ ] **P5C.06** Replace tests of removed internals with tests of claim exclusion, durable continuity, cancellation, cleanup, and public events. Preserve regression scenarios.

### 5D. Selective Daytona SDK/Toolbox filesystem adoption

**Files:** `daytona/workspace_agent/`, workspace gateway, storage, artifact and memory-promotion adapters. Audit required guarantees before replacement; do not delete atomicity, path safety or locking simply because a similarly named SDK method exists.

- [ ] **P5D.01** Inventory each Workspace Agent operation and its required guarantees: path confinement, symlink handling, bounded reads, checksum verification, lock ownership, compare-and-swap, atomic replacement, and publication ordering.
- [ ] **P5D.02** Use public `sandbox.fs` upload/download/list/stat/search APIs where they preserve those guarantees. Use streaming/batched operations where they materially reduce copying or network round trips.
- [ ] **P5D.03** Preserve custom bounded local helpers for atomic memory/promotion or race-resistant filesystem operations not provided equivalently by the SDK. SDK file upload is not automatically a transactional publish primitive.
- [ ] **P5D.04** Keep model-driven repeated parsing/search close to the data in sandbox Python. Do not route every local read/grep through a host network request.
- [ ] **P5D.05** Verify transfer cancellation, size limits, checksums, duplicate requests, and per-file batch errors. Keep client timeout distinct from confirmed server stop.
- [ ] **P5D.06** Reduce Workspace Agent source transfer/handshake/duplicate caches only after equivalent tests pass. Record operations removed and network calls saved.

- [ ] **P5M.01** Preserve linked preparation/execution trace compatibility while adding runtime variant, program/image identity and Fleet Run correlation. Record final Fleet settlement status separately from individual span status.
- [ ] **P5M.02** Use existing phase spans to measure claim/preparation, RLM execution, result/artifact validation, durable commit and immediate cleanup. Log later recovery cleanup in separately linked operational work; do not keep a user trace open indefinitely.
- [ ] **P5M.03** Ensure a completed model answer cannot produce a committed-success observation before Fleet settlement succeeds. Conversely, exporter failure after commit cannot turn a completed Fleet Run into a failed Run.
- [x] **P5M.04** Test trace correlation and parentage across two sequential Turns in one Session and concurrent Runs in different Sessions. Verify no callbacks, tools, proxies or trace context survive into the wrong invocation.
- [ ] **P5M.05** Compare tracing-on/off overhead and broker/native results using Phase 1.1 comparison specifications. Retain rollback and deletion evidence in MLflow without requiring MLflow availability to execute rollback.

**Exit:** each Run has fresh execution state; authorized database/Volume data suffices after process/context/sandbox replacement; uncontained work prevents unsafe reuse; replaced resident and broker execution code is removed after the bounded rollback gate.

## Phase 6 - Recursive RLM v2

**Objective:** implement the full recursive redesign, not merely preserve the old child launcher. Recursion must supply task-relevant evidence with bounded cost and smaller selected context.

### 6A. SubproblemCapsule and restricted child inputs

**Files:** `rlm/recursion.py`, existing serializable inputs, `daytona/recursive_child_runtime.py`, Phase 4 profiles and recursive tests.

- [x] **P6A.01** Introduce one strict JSON/Pydantic `SubproblemCapsule`: task, selected fragments, authorized references, expected result shape, evidence requirements, and bounded child allocation.
- [x] **P6A.02** Cap total serialized bytes, fragment count, reference count, prompt size, and returned output. Make encoding deterministic and checksum selected files where applicable.
- [x] **P6A.03** Remove automatic request/full-history/Session-context copying into every capsule child. Preserve the parent's committed-history interface; do not compact or rewrite DSPy's native REPLHistory.
- [x] **P6A.04** Build capsules from explicitly selected data. Treat retrieved documents, workspace memory, and child outputs as untrusted input, not execution policy.
- [x] **P6A.05** Distinguish pure DSPy sub-LM calls from recursive RLM children: `llm_query` needs no additional sandbox. Allocate an isolated child sandbox only for another iterative code/LM loop.
- [x] **P6A.06** Use the lean Volume-less profile for selected-context children. Stage bounded file copies when necessary; use workspace-mounted children only when the actual task requires them.
- [x] **P6A.07** Enforce authorized path/scope checks. Do not assume a Volume subpath or context working directory is a complete read-only/tenant-isolation boundary.
- [x] **P6A.08** Keep depth one. Child built-in semantic calls remain allowed under the same global ledger; deeper native delegation remains disallowed or follows the existing bounded fallback.

### 6B. Structured scheduling and typed outcomes

Preserve ordering and existing failure policy while replacing scheduling mechanics. Change partial-result policy only in a separate reviewed step, with a versioned tool/result contract where needed.

- [x] **P6B.01** Reuse `RecursiveRLMExecutor` and its metrics/reservations. Replace nested private event loops with an application-owned scheduler and one bounded child semaphore; keep the required sync bridge.
- [x] **P6B.02** Preserve ordered all-or-nothing batch semantics for the initial scheduler replacement. This isolates concurrency changes from result-policy changes.
- [x] **P6B.03** Preserve monotonic global admissions: retries and failed work are charged. Do not introduce refundable token/sandbox sub-budgets unless the accounting model genuinely supports them.
- [x] **P6B.04** Validate `recursion_max_parallel_children <= recursion_max_calls`; effective limits have one settings source and are checked by tests.
- [x] **P6B.05** Propagate parent cancellation, deadline, and authorization loss to queued and running children; prevent new acquisitions after revocation.
- [x] **P6B.06** Await child cleanup or establish a durable fenced cleanup obligation before parent success. A successful child answer cannot excuse an uncontained mutating worker.
- [x] **P6B.07** Add typed per-child outcomes: completed, failed, timeout, cancelled, with bounded answer, evidence references, usage, and error category.
- [ ] **P6B.08** Permit partial sibling evidence only for explicitly read-only analysis after its safety cleanup succeeds. Authorization/isolation/cleanup failures remain fatal. Do not swallow CancelledError as an ordinary partial result.

### 6C. Prove the recursive advantage

- [ ] **P6C.01** Compare a direct-prediction baseline, native RLM without Fleet recursive tools, current native-child behavior, and capsule children on matched tasks. Stock RLM already has native sub-LM tools; do not label two identical variants as separate baselines.
- [ ] **P6C.02** Initially hold models, task inputs, context access, and budgets constant. Evaluate cheaper sub/child model roles as a separate experiment.
- [ ] **P6C.03** Measure task correctness, evidence validity, completion rate, bytes delegated, root/child calls, tokens where known, sandbox seconds, end-to-end latency, and cost per successful task.
- [ ] **P6C.04** Include tasks where recursion should not be used. Score unnecessary sandbox/LM fan-out as inefficiency, not a successful capability demonstration.
- [ ] **P6C.05** Record actual native child invocations. A lifecycle benchmark that made zero recursive LM calls cannot prove recursion quality.
- [ ] **P6C.06** Keep the parent as final synthesis/verification authority. Enable recursive defaults only for task classes with retained quality/cost evidence.

- [ ] **P6D.01** Integrate the Phase 4 SemanticChild profile with eligible warm acquisition and cold fallback. Use selected uploaded files or a verified restricted Volume profile only for WorkspaceChild. Never mount the whole Workspace just to make a child easier to implement.
- [ ] **P6D.02** Preserve the decision order: deterministic Python, one native semantic call, native semantic batch, then an isolated iterative child only when justified. Express guidance in the existing program/tool contracts rather than adding a mandatory planning-model call.
- [x] **P6D.03** Return typed child evidence including completion status, bounded answer, source references, verified observations, uncertainty/failure category and usage. Keep full private trajectories out of parent-facing responses and public receipts.
- [x] **P6D.04** Give queued and executing siblings a bounded allocation beneath the shared parent ledger. Admission limits are not memory isolation; enforce sandbox resources separately and preserve reserved root finalization capacity.
- [ ] **P6D.05** Test conflict/disagreement between children, missing evidence, unneeded recursion, repeated identical subproblems, oversized capsules, and child output limits. Root must verify/synthesize and remain the sole final publication authority.

- [ ] **P6M.01** Correlate each native child with parent Fleet Run, parent span or trace link, child call ID/index, depth, child profile and actual execution mode. Built-in sub-LM calls must not be counted as native child RLMs.
- [ ] **P6M.02** Record child queue/acquisition/execution/cleanup durations, selected input bytes, result bytes, root/child/sub-LM attempt counts and known token usage. Sum non-overlapping usage, not nested aggregate totals; root wall time is not the sum of parallel child durations.
- [ ] **P6M.03** Publish matched MLflow ablations for direct prediction, native RLM without Fleet recursive tools, existing Fleet recursion and capsule recursion. Keep task access and total budgets comparable; separate model-role and warm-capacity experiments.
- [ ] **P6M.04** Score answer correctness and evidence validity independently of tool use. A tool span proves a call occurred, not that the answer is supported; verify references and actually used evidence.
- [ ] **P6M.05** Activate paid child capacity and broader recursive defaults only for workloads with demonstrated quality/cost value. Attach raw sample counts, failures and statistical uncertainty; a demo that launched no recursive LM call cannot close this gate.

**Exit:** native children receive bounded selected inputs; depth and shared budgets hold; ordered structured concurrency is simpler; partial results never bypass containment; MLflow evidence demonstrates when recursive work is better than the simpler alternatives.

## Phase 7 - Evaluation, MLflow/GEPA optimization, rollout, and final deletion

**Objective:** connect existing MLflow quality tooling to the stable runtime, then promote only reproducible, evaluated program/configuration candidates.

### 7A. Stable DSPy evaluation and optimization

**Files:** `optimization/`, `scripts/benchmarks/`, existing metrics and datasets. The existing GEPA smoke is development-only and non-promotable; real candidate execution and held-out evidence are a separate path.

- [ ] **P7A.01** Reuse the evaluation/optimization package and current benchmark evidence. Use deterministic checks for schema, artifact bytes, calculations, budgets, and evidence references; use labelled semantic evaluation only for qualities that require it.
- [ ] **P7A.02** Create separate train/development/held-out sets. Isolate every evaluation's interpreter, files, caches, tool side effects, and per-Run budget; optimizer parallelism also has a global experiment budget.
- [ ] **P7A.03** Tune limits and model roles one dimension at a time. Start with an appropriate cheaper sub-LM; do not assume one model is best for root and all children.
- [ ] **P7A.04** Use `dspy.Evaluate` and the existing result/metrics pipeline for comparable program evaluation. Keep failures in the score denominator and retain sample counts/uncertainty.
- [ ] **P7A.05** Extend the existing GEPA development/optimization machinery to evaluate real program candidates only after meaningful metrics, feedback-rich data and sandbox limits exist. Retain the smoke path as explicitly non-promotable. Optimize stable task/route/capsule instructions, not authorization rules or infrastructure source.
- [ ] **P7A.06** Prove a saved optimized program can be reloaded into a fresh Turn with newly bound tools/interpreter/budget. Never serialize live clients, closures with credentials, locks, or resident runtime state.
- [ ] **P7A.07** Store program/config/model/DSPy/SDK/image/dataset/scorer identities with promotion evidence. Rollback selects a previous evaluated configuration, not guessed prompt text.
- [ ] **P7A.08** Keep Flex, native grandchildren, GPU execution, snapshot forks, and computer-use tooling out of this migration unless a separate evaluated task justifies them.

### 7B. One dataset/scorer contract, two complementary evaluation surfaces

`dspy.Evaluate` is the in-process DSPy program/metric evaluator. `mlflow.genai.evaluate` supports engineering assessment and result comparison. They must share dataset identity, scores and outcomes rather than execute the same mutating task twice just to satisfy two dashboards.

- [ ] **P7B.01** Reuse `rlm_eval_dataset.py`, `scorers.py`, `judges.py`, and the existing evaluation runner. Version the task schema, dataset digest, split, scorer implementation/configuration and expectation-bearing source records.
- [ ] **P7B.02** Share pure deterministic scoring functions between DSPy metrics and MLflow scorer adapters. Adapt the signatures at the boundaries without duplicating business rules.
- [ ] **P7B.03** Prefer evaluating retained outputs/traces when comparing the same execution. Use a fresh isolated evaluation environment only when new inference is intended. Do not let MLflow predict_fn replay mutations against the original workspace.
- [ ] **P7B.04** Account for the current linked preparation/execution trace topology: a MLflow prediction-function integration must meet its documented single-trace-per-call contract, or use the existing outputs/traces scoring path with explicit Fleet Run grouping. Do not add a wrapper trace and accidentally create three competing roots.
- [ ] **P7B.05** Curate trace-origin examples only with explicit eligibility, permission, verified expected outputs/evidence, and data minimization. Do not automatically turn all production conversations into optimizer training data.
- [ ] **P7B.06** Separate train, selection/development and held-out examples at the Session/project level where needed to avoid leakage. Preserve failed, missing-trace and unscorable cases with explicit status, rather than dropping them or calling them passes.
- [ ] **P7B.07** Preserve the existing local evaluation lane and configured Databricks/Unity Catalog quality-of-record lane. Keep the documented Python 3.12 managed-dataset lane until exact dependencies certify a shared version; do not force that dependency set into the main runtime.
- [ ] **P7B.08** Run independent evaluation jobs in isolated processes when MLflow global settings/thread-safety require it. Bound judge concurrency, judge attempts/cost and dataset transfer separately from the evaluated Turn budget.

### 7C. Human feedback, judge alignment and sampled monitoring

- [ ] **P7C.01** Reuse `align_judges.py` and existing labeling/assessment flows. Calibrate semantic correctness and evidence-coverage judges with human-reviewed examples before treating their scores as release gates.
- [ ] **P7C.02** Version judge model, instructions, alignment data and sampling configuration. Re-score the same retained baseline outputs after a judge change; distinguish judge drift from agent regression.
- [ ] **P7C.03** Reuse `enable_monitoring.py` in supported configured backends only. Treat server-side monitoring availability as a capability check, not a universal OSS feature. Sample rates and compute spend remain explicit operator policy.
- [ ] **P7C.04** Keep post-hoc quality monitoring outside the user Turn path and separate from runtime safety controls. Trace retention sampling and judge sampling are different policies; report the evaluated denominator.
- [ ] **P7C.05** Surface regressions in correctness, evidence, latency, usage, recursion effectiveness, cleanup backlog and export failures. Monitoring can produce a review signal; it must not change prompts, models or runtime variants automatically.

### 7D. Prompt/program lineage and safe promotion

- [ ] **P7D.01** Reuse `manage_prompts.py` for versioning and linking canonical instructions, and `annotate_traces.py` only for bounded derived metadata not already captured. Registration must not overwrite the source-of-truth runtime policy.
- [ ] **P7D.02** Record each real GEPA experiment as a separate MLflow tracking run with candidate program/instruction digest, parent candidate, dataset/split/scorer versions, reflection model, evaluation budgets and safe result artifacts.
- [ ] **P7D.03** Keep development smoke, real candidate execution, selection evidence and held-out promotion evidence as different statuses. An aggregate smoke receipt is not an optimized deployable program.
- [ ] **P7D.04** Default to safe validated instruction/program-state artifacts, not pickling a live Fleet RLM with tools, LM credentials, clients or locks. Use MLflow DSPy model packaging only after proving serialization and fresh-runtime loading are safe.
- [ ] **P7D.05** Test loading before serving or in an isolated certification process. MLflow documents that its DSPy load_model restores global DSPy settings; do not call it during concurrent Runs or let it override application configuration.
- [ ] **P7D.06** Resolve any registry alias once during an explicit release/startup into an immutable version/digest. Keep that identity fixed through the Run. A remote alias update must not hot-swap in-flight code or prompts.
- [ ] **P7D.07** Require held-out quality, operational, budget, privacy and cleanup gates before promotion; retain the previous immutable configuration for rollback. No automatic infrastructure or credential-policy optimization.

### 7E. Staged rollout and final subtraction

- [ ] **P7E.01** Roll out SDK upgrade, native Turn-scoped runtime, capsule recursion, and warm capacity independently. SDK upgrade does not imply enabling all other features.
- [ ] **P7E.02** Never shadow-replay arbitrary user writes into the same live workspace. Use read-only recordings, copied workspaces, or controlled canary Sessions.
- [ ] **P7E.03** Gate promotion on public-contract parity, no claim/authorization regressions, confirmed resource containment, task quality, failure rate, per-scenario latency, usage, and cleanup backlog.
- [ ] **P7E.04** Rehearse rollback with additive migrations and compatible bindings. Drain or fence active Runs before switching runtime behavior; do not switch an in-flight Run midway.
- [ ] **P7E.05** Regenerate public schemas/TUI types only from their sources where necessary. Verify the existing client renders native execution, child results, failures, cancellation, and final output without a separate UI execution model.
- [ ] **P7E.06** Remove the legacy selector and implementation once rollback evidence and release history are retained. Do not leave obsolete modes as permanent user features.
- [ ] **P7E.07** Remove migration-only aliases such as `RLMProgramSpec` only after imports migrate; remove obsolete settings, broker execution code, resident-state tests, and historical claims in docs.
- [ ] **P7E.08** Split remaining large modules only by distinct ownership/reason to change. Prefer moving model configuration out of `program.py` to another existing/small RLM module over introducing a new framework package.
- [ ] **P7E.09** Retire unused snapshots only after no active binding/pool needs them. Do not delete workspace Volumes as ordinary Session cleanup.
- [ ] **P7E.10** Publish a subtraction report: state owners removed, private upstream dependencies remaining, duplicate transports eliminated, lines/files removed, benchmark outcomes, and known limitations.

- [ ] **P7F.01** Give SDK upgrade, native runtime, capsule recursion, warm capacity and optimized instructions their own rollout cohorts and evidence. Tag them with source/config identities so trace and benchmark comparisons remain interpretable.
- [ ] **P7F.02** Retain complete safe release receipts outside ephemeral scratch paths and optionally mirror them into MLflow. A tracing/export outage may block evidence-based promotion, but must not fail a user Turn or prevent a safe rollback.
- [ ] **P7F.03** After stable behavior, evaluate moving full evaluation/registry dependencies out of the serving install and using a smaller tracing distribution only if the exact DSPy integration remains compatible. This is optional packaging subtraction, not a prerequisite new migration.
- [ ] **P7F.04** Remove duplicated MLflow configuration and obsolete post-hoc tagging where canonical attributes now exist. Preserve the narrow compatibility and sanitization code still justified by tests.

**Exit:** one supported production path, reproducible approved program configuration, calibrated quality evidence, bounded observability and no orphaned migration flags or execution owners remain.

## 5. MLflow identity, trace, and metric contract

### 5.1. Do not confuse the identifiers

| Term | Meaning | Relationship |
| --- | --- | --- |
| Fleet Session | Durable conversation/workspace association | Groups multiple user interactions |
| Fleet Turn | Conversational request/result unit | May require an execution attempt; preserve current user/assistant row encoding |
| Fleet Run | Application execution attempt with claim/settlement authority | Existing `fleet_runs` identity; never an MLflow run |
| MLflow trace | Instrumented request/phase execution | Current code has linked preparation/execution traces per Fleet Run |
| MLflow span | Timed operation inside a trace | Module, LM call, tool, interpreter operation or lifecycle phase |
| MLflow tracking run | Campaign/candidate experiment record | Collects parameters, metrics and engineering artifacts; not required per user Turn |
| Child call ID | One admitted native recursive invocation | Correlated to parent Fleet Run and its execution evidence |
| MLflow assessment | Human/scorer feedback on retained evidence | Post-hoc observation; cannot change Fleet settlement |
| MLflow artifact | Engineering file attached to an experiment | Different from a user-facing Fleet Artifact committed through Fleet policy |

### 5.2. Logical observability structure

Keep current preparation/execution trace linkage initially. The following is a logical grouping, not a requirement to rename existing spans or force all phases into one root:

```text
Fleet Run correlation
  preparation trace
    claim and authorized input preparation
    Session sandbox acquisition / prewarm
  execution trace (linked to preparation)
    native DSPy RLM
      root LM actions
      interpreter actions
      native llm_query / llm_query_batched calls
      Fleet host tools
      Fleet recursive child calls
        child acquisition / execution / cleanup
    result validation and durable settlement
    immediate cleanup
  separately linked recovery observations, if cleanup outlives the request
```

Existing DSPy autolog spans are the default LM/module/tool instrumentation. Add manual spans only for Fleet-owned lifecycle and unrepresented operations. Runtime Events stay on their existing SSE/TUI path. A successful RLM prediction is not a committed Turn, and a completed trace is not proof that remote processes stopped.

### 5.3. Proposed non-secret identity attributes

Reuse existing equivalent keys; the following names are a Fleet schema proposal, not claims of MLflow-reserved attributes:

```text
fleet.run_id
fleet.runtime_variant
fleet.program_digest
fleet.snapshot_profile
fleet.snapshot_digest
fleet.dspy_version
fleet.daytona_version
fleet.mlflow_version
fleet.evidence_lane
fleet.parent_run_id / fleet.child_call_id / fleet.recursive_depth
fleet.settlement_status
fleet.cleanup_status
```

Use MLflow's supported Session/user correlation fields for opaque authorized identifiers where appropriate. Keep high-cardinality IDs out of aggregate metric dimensions. Never put API keys, Volume paths, raw sandbox identifiers or full prompts into metric names/tags. Unknown metrics remain unknown.

### 5.4. Measurement responsibilities

| Measurement group | Required observations |
| --- | --- |
| End-to-end | Fleet Run wall time, committed/failed/cancelled/timeout status, per-scenario sample count |
| LM | Admitted attempts, observed physical attempts when available, role-specific known input/output tokens, cache state, repair/finalization counts |
| Interpreter | Context startup, first-action and subsequent-action latency, output bytes, execution errors, timeout and containment outcome |
| Sandbox | Cold/start/prewarm/warm category, verified readiness, sandbox seconds, unused prewarm time, quota/fallback events |
| Recursion | Native children actually launched, selected input bytes, child result bytes, queue/execution/cleanup latency, evidence validity, unnecessary fan-out |
| Persistence | Claim/commit/recovery latency, contention classification, checksum/publication outcome |
| Observability | Export failures, dropped traces, redaction failures, queue/flush behavior, tracing overhead |
| Quality/cost | Correctness, evidence coverage and validity, completion, uncertainty/sample count, cost per successful task |

Do not sum nested aggregate token values or parallel child durations into parent wall time. Use non-overlapping leaf usage and the existing budget/result accounting. Estimated cost must carry its pricing basis/date; unavailable price or usage does not mean zero. Sampling cannot support exact global failure rates without the relevant denominator.

### 5.5. Privacy and availability

Metadata-only operational tracing should remain useful. Approved restricted evaluation records may contain minimized task inputs, outputs and evidence; public receipts remain content-free. Credentials and provider hidden reasoning never belong in either. Exported span events and exception payloads need the same treatment as inputs/outputs. Sanitization must happen before data becomes eligible for export; silently continuing on a failed sanitizer is not a safe fallback.

MLflow server failure must not change Fleet execution or committed history. Missing evidence must, however, remain visible to a release reviewer: no data is not a passing promotion gate. Keep managed monitoring, dataset import, judge alignment, and prompt alias writes out of user Turn execution.

## 6. Review/merge sequence

| Order | Scope | Canonical phase |
| --- | --- | --- |
| 1 | Exact Daytona 0.210.0 upgrade and typed-error adoption | 1.1A |
| 2 | Remaining actual-main schema integrity and migration reconciliation | 1 |
| 3 | Benchmark comparison axes and retained legacy baseline | 1.1B |
| 4 | MLflow exact-version, privacy, lifecycle and export certification | 1.1C |
| 5 | Existing DSPy budget/proxy/adapter/tool certification | 2 |
| 6 | Native interpreter and smallest working host-tool route | 3A-B |
| 7 | Native output, cancellation, containment and trace parity | 3C / 3M |
| 8 | Environment profiles, image manifests and operator commands | 4A-B |
| 9 | Prewarm/warm-pool policy, eligibility and authorized capacity canary | 4C / 4M |
| 10 | Session compute ownership, resource fencing and durable cleanup | 5A |
| 11 | Complete native Turn-scoped path and rollout comparison | 5B / 5M |
| 12 | Resident RLM and execution-broker subtraction | 5C |
| 13 | Equivalent SDK/Toolbox filesystem operations | 5D |
| 14 | Capsules and restricted child profiles | 6A / 6D |
| 15 | Scheduler replacement preserving batch behavior | 6B, first change |
| 16 | Typed outcomes and controlled partial-result behavior | 6B, separate change |
| 17 | Recursive-value ablation and measured warm activation | 6C / 6M |
| 18 | Shared evaluation, assessments, judge alignment and monitoring | 7A-C |
| 19 | Real GEPA evidence, prompt/program lineage and safe promotion | 7D |
| 20 | Rollout/rollback, legacy deletion and optional packaging subtraction | 7E-F |

Split any row further when required for a reviewable diff. This table is sequencing guidance, not a fixed PR-count target. Existing correct implementations remain intact until their replacements are proven.

## 7. Validation commands and retained evidence

Use current repository commands from `AGENTS.md`, scoped to the changed contract:

```bash
uv run pytest <relevant-tests> -q
uv run ruff check <changed-paths>
uv run ruff format --check <changed-paths>
uv run ty check src
make check-codebase-tree
make check-dependency-boundaries
make api-sync
make api-check
make tui-check
make check-docs
make check
git diff --check
```

`api-sync`/`api-check` apply to public/generated-contract changes; `tui-check` to TUI changes; full `make check` to cross-cutting work. Use the existing explicit live entry points for credentialed lanes. The plan itself was prepared through source inspection, not by running these checks.

Evidence should record: source revision and dirty state, exact installed dependencies, selected non-secret policy, runtime variant, profile/image identities, scenario/scorer identity, repetitions, successes/failures, measured usage and lifecycle distributions, exercised/skipped/blocked gates, and sanitized failure categories. Sensitive prompts, raw reasoning, provider responses, credentials, and private storage references do not belong in public receipts.

Additional MLflow-focused validation should extend the already present suites, including:

```bash
uv run pytest tests/unit/backend/test_mlflow_runtime.py   tests/unit/backend/test_mlflow_tracing_config.py   tests/unit/backend/test_turn_tracing.py   tests/unit/backend/test_turn_trace_phase_link.py   tests/unit/backend/test_validate_mlflow_tracing.py   tests/contracts/backend/test_mlflow_lifespan.py   tests/unit/optimization/test_mlflow_observability.py -q

uv run pytest tests/unit/optimization   tests/unit/scripts/test_align_judges.py   tests/unit/scripts/test_enable_monitoring.py   tests/unit/scripts/test_rlm_eval_dataset.py   tests/unit/scripts/test_annotate_traces.py   tests/unit/scripts/test_manage_prompts.py   tests/unit/scripts/test_scorers.py -q
```

Verify paths against the implementation checkout before execution. Add meaningful trace parentage, redaction-failure, duplicate-usage, late-cleanup and exporter-outage scenarios to those suites. Do not create a disconnected observability harness. The command examples are validation instructions, not results from this documentation revision.

## 8. Final acceptance checklist

Checked entries below have local implementation or contract evidence; they do
not authorize native cutover or paid live capacity. Unchecked entries remain
required certification or rollout gates.

- [ ] **AC.01** Daytona 0.210.0 is the sole certified SDK target and DSPy remains 3.3.1.
- [x] **AC.02** The existing budget/proxy/spec/output implementations are reused rather than duplicated.
- [ ] **AC.03** DB lineage and concurrent Run authority hold on actual supported schema versions.
- [ ] **AC.04** A Session survives process/context/sandbox replacement through committed metadata and authorized durable files.
- [ ] **AC.05** Every Turn gets fresh Python/model/tool/callback state; one context still retains state across that Turn's RLM iterations.
- [ ] **AC.06** Remote cancellation and bounded output are proven, not inferred from local timeouts.
- [x] **AC.07** A missing file cannot trigger whole-sandbox recreation.
- [x] **AC.08** Native tools remain native; Fleet's gateway only transports and authorizes callbacks.
- [x] **AC.09** Child RLMs receive bounded selected context, not the whole Session by default.
- [x] **AC.10** No native grandchildren or unnecessary sandboxes are introduced for pure sub-LM calls.
- [ ] **AC.11** Warm pools are optional, eligible, quota-aware, and never receive used tenant sandboxes back.
- [ ] **AC.12** Workspace I/O simplification preserves atomicity, locking, scope, and checksum guarantees.
- [x] **AC.13** Runtime Event/TUI contracts remain coherent.
- [x] **AC.14** Live quality and performance evidence are distinct from scripted lifecycle evidence.
- [ ] **AC.15** Obsolete resident registries, fingerprints, execution broker paths, aliases, and flags are deleted when their replacements pass.

- [x] **AC-MLFLOW.01** Existing MLflow lifecycle, tracing, evaluation, prompt, monitoring and optimization owners are reused; no second observability stack exists.
- [ ] **AC-MLFLOW.02** Fleet Run, MLflow tracking run, trace, span and child identities are explicit and correctly correlated under concurrency.
- [x] **AC-MLFLOW.03** No duplicated LM/token accounting, one-span-per-event noise, or trace-driven settlement is introduced in the locally tested paths.
- [ ] **AC-MLFLOW.04** Exporter outage, queue pressure, redaction failure and bounded shutdown flush do not fail or stall user Turns; unsafe content is never silently exported.
- [ ] **AC-MLFLOW.05** SDK/interpreter/image/capsule migrations have retained MLflow-linked campaign evidence with honest exercised/blocked/unknown status.
- [ ] **AC-MLFLOW.06** Evaluation and GEPA share versioned data and scoring, do not replay unsafe side effects, and preserve the non-promotable smoke distinction.
- [ ] **AC-MLFLOW.07** Approved program/prompt identity is immutable during each Run; loading and promotion never mutate global DSPy settings during concurrent execution.
- [x] **AC-MLFLOW.08** Managed quality tooling remains capability-checked and optional to local execution; source-of-truth Session data stays outside MLflow.

## 9. Source references

Repository observations above are pinned to the reviewed commit, not inferred from an old phase-completion statement.

- Current main identity: https://github.com/Qredence/fleet-rlm/commit/bcb85cc7b29d625e4c399cbf0a56459d0617302e
- Current status/roadmap: [ADR 006 implementation-status ledger](docs/decisions/006-implementation-status.md); the prior scratch roadmap is historical input only.
- Dependencies: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/pyproject.toml
- Program and proxy: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/rlm/program.py
- Budget: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/rlm/budget.py
- Output contract: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/rlm/output_contract.py
- Runtime ownership: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/rlm/runtime.py
- Recursion: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/rlm/recursion.py
- ORM: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/persistence/models.py
- Lineage migration: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/migrations/versions/019fdb010001_enforce_turn_run_session_lineage.py
- Benchmark runner: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/scripts/benchmarks/runtime_v2.py
- Provisioning/image builder: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/daytona/provisioning.py
- Platform adapter: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/daytona/platform.py
- Exact Daytona interpreter implementation: https://github.com/daytona/clients/blob/v0.210.0/sdk-python/src/daytona/_async/code_interpreter.py
- Exact Daytona Volume implementation: https://github.com/daytona/clients/blob/v0.210.0/sdk-python/src/daytona/_async/volume.py
- Daytona changelog: https://www.daytona.io/changelog
- Daytona builder: https://www.daytona.io/docs/en/declarative-builder/
- Daytona warm pools: https://www.daytona.io/docs/en/warm-pools/
- Daytona Agent Skill: https://www.daytona.io/docs/en/agent-skills/
- Daytona MCP: https://www.daytona.io/docs/en/mcp/
- DSPy RLM: https://dspy.ai/api/modules/RLM/
- DSPy LM: https://dspy.ai/api/models/LM/
- DSPy evaluation: https://dspy.ai/api/evaluation/Evaluate/
- DSPy GEPA: https://dspy.ai/api/optimizers/GEPA/overview/

Additional source inspected for the MLflow workstream (repository references retain the same reviewed commit):

- MLflow lifecycle: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/observability/mlflow.py
- Trace configuration, linked phase traces and export handling: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/observability/tracing.py
- Existing GEPA smoke observability: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/src/fleet_rlm/optimization/mlflow_observability.py
- Existing quality/registry/monitoring workflow: https://github.com/Qredence/fleet-rlm/blob/bcb85cc7b29d625e4c399cbf0a56459d0617302e/docs/how-to-guides/evaluation-optimization.md
- MLflow DSPy autolog and model load/save API: https://mlflow.org/docs/latest/api_reference/python_api/mlflow.dspy.html
- MLflow DSPy tracing integration: https://mlflow.org/docs/latest/genai/tracing/integrations/listing/dspy/
- MLflow trace user/Session correlation: https://mlflow.org/docs/latest/genai/tracing/track-users-sessions/
- MLflow production tracing and export controls: https://mlflow.org/docs/latest/genai/tracing/prod-tracing/
- MLflow sensitive-data handling: https://mlflow.org/docs/latest/genai/tracing/observe-with-traces/masking/
- MLflow evaluation API: https://mlflow.org/docs/latest/api_reference/python_api/mlflow.genai.html
- MLflow evaluation and monitoring overview: https://mlflow.org/docs/latest/genai/eval-monitor/
- MLflow agent evaluation: https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/agents/
- MLflow prompt registry: https://mlflow.org/docs/latest/genai/prompt-registry/

Official rolling documentation was checked for the proposed API roles. Exact installed-version behavior and backend feature availability remain certification gates, particularly for DSPy/MLflow compatibility, managed datasets/monitoring, warm pools and cancellation semantics.
