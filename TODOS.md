# Fleet RLM Complexity Reduction Implementation Plan

> **For agentic workers:** Implement tasks in order. Keep this file unchanged during execution, mark progress only when explicitly requested, and preserve unrelated user changes. Use the closest applicable `AGENTS.md` and the repository's existing validation commands.

## Goal and Current Evidence

Simplify the canonical Python backend under `src/fleet_rlm/` by removing proven dead ends, tightening existing seams, centralizing duplicated Turn Claim policy, and reducing the maintenance cost of the Daytona and RLM execution hotspots without changing product behavior or public wire contracts.

The review measured the current checkout before this plan was written:

- `src/fleet_rlm/` contains 18,508 Python lines.
- Seven modules exceed 500 lines:
  - `persistence/repositories/turns.py` - 820 lines.
  - `daytona/session_manager.py` - 775 lines.
  - `daytona/workspace_fs.py` - 636 lines.
  - `sessions/committed_turn.py` - 540 lines.
  - `daytona/run_environment.py` - 534 lines.
  - `rlm/runner.py` - 527 lines.
  - `chat/turn_coordinator.py` - 502 lines.
- `uv run ruff check src/fleet_rlm --select C901` reports 28 complexity findings across 20 files.
- The highest-churn current modules include `rlm/runner.py`, `chat/turn_coordinator.py`, `daytona/workspace_fs.py`, `daytona/session_manager.py`, `persistence/repositories/turns.py`, and `daytona/run_environment.py`.
- The optional Turn observability pipeline is disconnected: `RLMRunner` stores an exporter it never reads, Daytona composition constructs and discards a `LoggingTurnExporter`, and the trace/export modules have no production callers.
- Turn Claim transition legality and receipt construction are repeated across the lifecycle module, coordinator, in-memory state adapter, SQL state adapter, and startup recovery.
- Several runtime branches exist solely because narrow test doubles do not satisfy already-declared protocols.

## Constraints

- Preserve the public FastAPI route inventory and HTTP request/response schemas.
- Preserve typed Runtime Events, AI SDK UI v1 SSE chunks, terminal ordering, and `[DONE]` behavior.
- Preserve the versioned `CommittedTurn` schema, replay semantics, and existing persisted-data compatibility.
- Preserve Turn Commit ownership in `TurnLifecycle.finish()` and stream ordering/cleanup ownership in `TurnCoordinator`.
- Preserve Deno and Daytona as the only public Run Environment profiles.
- Keep Daytona SDK imports inside `fleet_rlm.daytona`.
- Keep DSPy construction and invocation behind the existing DSPy contract modules; continue using `await rlm.acall(**named_inputs)`.
- Keep Attachments immutable, Artifacts commit-gated, Session Workspace writes immediate, and Result Snapshots private and Daytona-only.
- Do not weaken provider-error sanitization, path safety, workspace isolation, claim fencing, or cancellation settlement.
- Do not hand-edit `openapi.yaml` or `tools/fleet-tui/src/generated/openapi.ts`; no generated-contract change is expected.
- Keep provider-backed validation distinct from deterministic offline validation.
- Do not introduce direct `litellm` usage.
- Do not add dependencies or change pinned runtimes.
- Do not commit, push, deploy, migrate a user database, or modify production data unless separately requested.
- Preserve unrelated staged, unstaged, and untracked work.
- Each task must leave the backend runnable and independently testable.

## Complexity Findings

### 1. Dead observability/exporter pipeline

`observability/exporters.py` and `observability/record.py` define a trace/export subsystem that is never connected to execution. `RLMRunner.__init__()` accepts and stores `turn_exporter` without reading it, while Daytona composition constructs a `LoggingTurnExporter` and immediately discards it.

**Deletion test:** Removing these modules makes unused complexity disappear rather than pushing behavior into callers. `observability/failure_diagnostics.py` is separate and remains live through the Turns route.

### 2. Runtime compatibility branches created by incomplete test doubles

`TurnCoordinator` uses `inspect.signature()` to decide whether a `TurnPreparation` accepts `deadline`. It also discovers heartbeat, claim revocation, and stream ownership methods dynamically even though the corresponding protocols already describe the maintained behavior. `TurnLifecycleModule` similarly uses `getattr()` fallbacks for state-store settlement methods.

**Deepening opportunity:** Make the declared interfaces authoritative and update tests to cross the same seam as production. Runtime modules should not carry branches solely to support incomplete private adapters.

### 3. Duplicated Turn Claim transition policy

Fail, settle, revoke, complete-settling, heartbeat, and receipt rules appear independently in `TurnLifecycleModule`, `InMemoryTurnStateStore`, `SqlAlchemyTurnStateStore`, `TurnCoordinator`, and recovery logic.

**Deepening opportunity:** Introduce one pure transition policy used by both storage adapters. The adapters retain atomic persistence and locking; the domain module owns legal transitions and typed decisions.

### 4. Mutable two-stage Daytona composition

`LiveKernelResources` owns provider clients and models but also lazily constructs Turn preparation. Production creates it with incomplete mutable fields and later calls `configure_preparation()`. Tests bypass its constructor with `object.__new__`, reinforcing optional branches that production does not need.

**Deepening opportunity:** Keep the resource holder focused on process-owned clients and construct a complete `TurnPreparationModule` explicitly in Daytona composition.

### 5. Embedded Session Workspace remote program

`DaytonaSessionWorkspaceFS._atomic_run()` builds a roughly 400-line Python program as a list of quoted strings. The external Workspace interface is appropriately deep, but the implementation is difficult to inspect, run locally, type-check, and test directly.

**Deepening opportunity:** Move the stdlib-only remote implementation into a packaged Python module with one bounded request/response interface. Keep the host adapter and public Workspace behavior unchanged.

### 6. Test-only Daytona lifecycle methods

`DaytonaSessionManager.stop()`, `start()`, `pause()`, `resume()`, `archive()`, and `restore()` have no production or documentation callers. Unit tests call them directly, but acquisition already owns the required resume/start/replace behavior.

**Deletion test:** Removing these methods eliminates surface area. Maintained lifecycle behavior remains covered through acquire, reuse, replacement, quarantine, and fencing flows.

### 7. Monolithic RLM execution loop

The nested generator in `RLMRunner.stream()` owns tool wrapping, observer binding, worker creation, polling, cancellation, deadline handling, relay draining, trajectory reconciliation, usage extraction, and outcome construction.

**Deepening opportunity:** Preserve the small `stream(context)` interface while concentrating worker monitoring and observation buffering in private implementations. Do not expose additional seams to callers.

## Deliberate Non-Changes

- Keep the exhaustive mappings in `api/sse.py`, `api/ui_message.py`, and `chat/committed_turn_events.py`. They project three distinct closed interfaces and provide compile/test pressure when a new event or committed part is added.
- Keep the strict `CommittedTurn` codec. Replacing explicit validation with generic reflection would weaken persisted-data guarantees.
- Keep `daytona/in_process.py`; it is a real second adapter used by DSPy/interpreter contract tests.
- Keep `main.py`; `pyproject.toml` names it as the FastAPI entrypoint.
- Keep bundled Skill scripts; they are package resources loaded through the Skill catalog rather than ordinary imports.
- Keep provider-specific `getattr()` normalization at the Daytona SDK seam where SDK response shapes genuinely vary. Remove only test-compatibility reflection from stable Fleet-owned interfaces.

## Implementation Progress - 2026-07-23

**Status:** Substantially implemented and validated offline. Remaining work is explicitly tracked below rather than inferred from completed neighboring tasks.

### Verified outcomes

- Removed the dead Turn trace/exporter modules, stale exports, runner argument, and discarded Daytona construction.
- Removed six test-only `DaytonaSessionManager` lifecycle methods.
- Replaced Fleet-owned reflection and optional-method discovery with strict preparation, lifecycle, heartbeat, and stream-ownership protocols.
- Removed lazy Daytona preparation and now constructs `TurnPreparationModule` explicitly in composition.
- Extracted the 460-line stdlib-only Workspace remote program into `daytona/workspace_agent.py`; `workspace_fs.py` now contains a bounded adapter.
- Added `chat/turn_claim.py` and routed in-memory and SQL fail, settle, revoke, complete, and heartbeat decisions through the shared pure policy.
- Split runner worker monitoring and execution phases while preserving `RLMRunner.stream(context)`; `ruff --select C901` passes for the changed runner.
- Updated architecture/source-layout ownership documentation.
- Preserved HTTP/OpenAPI/TUI generated contracts and introduced no direct `litellm` imports.

### Actual implementation diff

The implementation and documentation changes total **1,330 additions and 1,250 deletions (net +80)**. This includes the three newly added files. The diff is deletion-heavy for dead code and movement-heavy for the Workspace implementation, as forecast.

### Validation receipts

- Passed: full offline backend/unit/contract/e2e test lane (`make test`).
- Passed: `make test-deno`, `make check-security`, `make build-release`, `make check-release`, and `make api-check`.
- Passed: repository Ruff, changed-file formatting, `ty`, runner `C901`, and `git diff --check`.
- Passed: `PLANS.md` and `TODOS.md` byte comparison after this progress update.
- Unchanged: `openapi.yaml` and `tools/fleet-tui/src/generated/openapi.ts`.
- Passed: `make check` and `make check-docs` after formatting `scripts/openapi_tools.py` and returning the root guide to its configured line budget.
- Pending: credentialed Daytona provider proof; no live execution was authorized.

### Intentionally remaining work

- Route lifecycle calls through a policy-owned action seam before removing residual decoding helpers.

## Expected Outcomes

| Area | Expected outcome | Verification signal |
|---|---|---|
| Dead code | The unused Turn trace/export pipeline and its misleading constructor surface are gone. | No references to `TurnTrace`, `TurnExporter`, `LoggingTurnExporter`, `safe_export`, or `turn_exporter`. |
| Turn interfaces | Production orchestration calls declared protocol methods directly. | No `inspect.signature()` preparation branch and no optional lifecycle/stream method discovery. |
| Turn Claim policy | In-memory and SQL adapters derive legal transitions from one pure policy. | One transition matrix passes against both adapters and recovery behavior. |
| Daytona composition | `LiveKernelResources` owns resources only; Turn preparation is explicitly assembled once. | No lazy `_preparation`, `configure_preparation()`, or `object.__new__(LiveKernelResources)` test setup. |
| Session Workspace | The remote filesystem implementation is directly executable and testable outside string generation. | Host adapter is small; agent tests cover path and write semantics. |
| Daytona lifecycle | Only lifecycle capabilities needed by acquisition, fencing, cleanup, and diagnostics remain. | No public manager methods that are exercised only by unit tests. |
| RLM execution | `RLMRunner.stream(context)` stays stable while worker/observation logic becomes locally understandable. | Changed runner entrypoints satisfy the configured complexity threshold and existing execution tests. |
| Wire compatibility | HTTP, SSE, Runtime Events, replay, and committed data do not change. | `make api-check` and existing contract suites remain green with no generated diff. |

## Expected Code Diff Inventory

Exact line totals must be recorded from the implementation diff rather than guessed in advance. The expected shape is deletion-heavy for dead code, movement-heavy for the Workspace agent, and behavior-neutral for lifecycle and runner refactoring.

| Operation | Target | Expected code diff |
|---|---|---|
| REMOVE | `src/fleet_rlm/observability/exporters.py` | Delete the unused 50-line exporter module. |
| REMOVE | `src/fleet_rlm/observability/record.py` | Delete the unused 84-line Turn trace module. |
| EDIT | `src/fleet_rlm/observability/__init__.py` | Remove stale trace/export exports; do not remove failure diagnostics. |
| EDIT | `src/fleet_rlm/rlm/runner.py:302-493` | Remove the unused exporter argument, then isolate observation and worker-monitor logic without changing `stream(context)`. |
| EDIT | `src/fleet_rlm/composition/daytona.py:65-174` | Remove the discarded exporter and explicitly construct complete Daytona Turn preparation. |
| EDIT | `src/fleet_rlm/daytona/session_manager.py:583-627` | Delete six lifecycle methods with no production callers. |
| EDIT | `src/fleet_rlm/chat/turn_coordinator.py:138-502` | Remove `inspect.signature()` and optional lifecycle/stream method discovery. |
| EDIT | `src/fleet_rlm/chat/turn_lifecycle.py:168-364` | Make the store interface strict and route claim actions through one typed transition operation. |
| EDIT | `src/fleet_rlm/chat/turn_preparation.py:86-87` | Require the coordinator-supplied absolute deadline. |
| EDIT | `src/fleet_rlm/daytona/run_environment.py:167-507` | Separate provider resource ownership from Turn preparation and remove lazy mutable wiring. |
| ADD | `src/fleet_rlm/daytona/workspace_agent.py` | Add the stdlib-only remote Session Workspace implementation as locally executable source. |
| EDIT | `src/fleet_rlm/daytona/workspace_fs.py:205-609` | Replace the embedded source builder with a bounded request/response adapter. |
| ADD | `src/fleet_rlm/chat/turn_claim.py` | Add the shared pure transition policy for fail, settle, revoke, complete, and heartbeat actions. |
| EDIT | `src/fleet_rlm/persistence/repositories/turns.py` | Map in-memory and SQL state through the shared Turn Claim policy while preserving atomic persistence. |
| EDIT | Relevant unit and contract tests | Replace incomplete test doubles, add adapter parity, and test the extracted Workspace agent. |

---

## Task 1 - Remove Proven Dead Ends

**Priority:** P0

**Files:**

- Remove: `src/fleet_rlm/observability/exporters.py`
- Remove: `src/fleet_rlm/observability/record.py`
- Edit: `src/fleet_rlm/observability/__init__.py:1-16`
- Edit: `src/fleet_rlm/rlm/runner.py:302-307`
- Edit: `src/fleet_rlm/composition/daytona.py:65-86,164`
- Edit: `src/fleet_rlm/daytona/session_manager.py:583-627`
- Edit: `tests/unit/backend/test_session_manager.py:667-711`

### Code changes

- [x] Delete the disconnected Turn trace and exporter modules.
- [x] Remove their package exports.
- [x] Remove `turn_exporter` from `RLMRunner.__init__()` and delete `_turn_exporter` state.
- [x] Remove the `LoggingTurnExporter` import and discarded construction from Daytona composition.
- [x] Delete `DaytonaSessionManager.stop`, `start`, `pause`, `resume`, `archive`, and `restore`.
- [x] Replace direct operator-method tests with behavior tests through acquire/reuse/resume/replacement where coverage is not already present.
- [x] Confirm `observability/failure_diagnostics.py` remains unchanged and importable.

### Expected behavior

- Turn execution, provider diagnostics, structured logging already produced elsewhere, and public failure handling remain unchanged.
- Daytona acquisition still restarts stopped Sandboxes, resumes paused Sandboxes, restores archived Sandboxes where supported, and replaces unrecoverable Sandboxes.
- The Python package exposes fewer unsupported internal interfaces.

### Focused validation

```bash
uv run pytest tests/unit/backend/test_session_manager.py tests/unit/backend/test_daytona_diagnostics.py tests/contracts/backend/test_turn_preparation_diagnostics.py -q
uv run ruff check src/fleet_rlm tests/unit/backend/test_session_manager.py
uv run ty check src/fleet_rlm
rg -n "TurnTrace|TurnExporter|LoggingTurnExporter|safe_export|turn_exporter" src tests
```

Expected: tests, Ruff, and `ty` pass; the final `rg` returns no matches.

---

## Task 2 - Make Fleet-Owned Protocols Authoritative

**Priority:** P1

**Files:**

- Edit: `src/fleet_rlm/chat/turn_preparation.py:86-87`
- Edit: `src/fleet_rlm/chat/turn_coordinator.py:5-9,49-58,138-224,405-502`
- Edit: `src/fleet_rlm/chat/turn_lifecycle.py:168-212,345-364`
- Edit: coordinator, lifecycle, cancellation, heartbeat, replay, and contract test doubles under `tests/unit/backend/` and `tests/contracts/backend/`

### Interface changes

- `TurnPreparation.prepare(turn, *, deadline: float)` requires an absolute deadline.
- `TurnLifecycle` declares `heartbeat_seconds` and `stale_after_seconds` as interface attributes.
- `TurnLifecycle.heartbeat`, `revoke_claim`, and `complete_settling` remain mandatory methods.
- `TurnEventStream.wait_owned()` becomes part of the maintained stream interface.
- Production code calls these methods directly without reflection or compatibility fallback.

### Code changes

- [x] Remove `inspect` and the `inspect.signature()` branch from `TurnCoordinator.open()`.
- [x] Pass `deadline=deadline` unconditionally to preparation.
- [x] Remove optional heartbeat discovery and use the declared lifecycle settings directly.
- [x] Remove `_revoke_claim()` fallback to `settle()`.
- [x] Call `stream.wait_owned()` directly during detached cleanup.
- [x] Remove `getattr()` fallbacks from `TurnLifecycleModule.settle`, `revoke_claim`, and `complete_settling`.
- [x] Update incomplete test doubles to implement the interface or use real lifecycle/preparation adapters with narrow spies.
- [x] Prefer shared protocol-complete fixtures only where several tests exercise the same stable seam; do not create a configurable mega-harness.

### Expected behavior

- Runtime behavior is unchanged for maintained adapters.
- Invalid private adapters fail during type checking or test setup instead of activating hidden production branches.
- Coordinator code has one preparation path, one heartbeat path, and one claim-loss path.

### Focused validation

```bash
uv run pytest \
  tests/unit/backend/test_turn_coordinator_commit.py \
  tests/unit/backend/test_turn_coordinator_failures.py \
  tests/unit/backend/test_turn_coordinator_replay.py \
  tests/unit/backend/test_turn_claim_heartbeat.py \
  tests/unit/backend/test_turn_lifecycle.py \
  tests/unit/backend/test_turn_lifecycle_cancellation.py \
  tests/contracts/backend/test_coordinator_runner_failures.py -q
uv run ruff check src/fleet_rlm/chat tests/unit/backend tests/contracts/backend
uv run ty check src/fleet_rlm
```

Expected: all maintained implementations and test adapters satisfy the same interface; public terminal ordering remains unchanged.

---

## Task 3 - Make Daytona Turn Preparation Explicit

**Priority:** P1

**Files:**

- Edit: `src/fleet_rlm/daytona/run_environment.py:167-507`
- Edit: `src/fleet_rlm/composition/daytona.py:65-174`
- Edit: `tests/unit/backend/test_live_turn_preparation.py`
- Edit: `tests/unit/backend/test_live_composition.py`
- Edit: `tests/contracts/backend/test_skill_turn_contract.py`
- Edit only as required: live Daytona tests that directly construct `LiveKernelResources`

### Target module shape

- `LiveKernelResources` owns Settings, provider client/platform, Volume client, binding store, admission control, Session manager, model bundle, tracked Sandbox ids, and engine disposal.
- `DaytonaEnvironmentProvider` receives `LiveKernelResources` explicitly.
- `DaytonaCapabilityPreparer` receives Settings, model bundle, Skill catalog, and required path/tool dependencies explicitly.
- `TurnPreparationModule` receives the production `AttachmentModule`, environment provider, and capability preparer in `build_daytona_composition()`.
- `TurnCoordinator` receives that complete preparation module rather than the resource holder.

### Code changes

- [x] Rename private preparer classes only if needed to make their explicit composition role clear; do not expose them as HTTP or package APIs.
- [x] Remove `_LiveAttachmentLifecycle` fallback behavior; production already supplies `AttachmentModule`.
- [x] Remove `LiveKernelResources.prepare()` and `configure_preparation()`.
- [x] Remove mutable `attachment_lifecycle`, `attachment_store`, `artifact_store`, `skill_catalog`, and `_preparation` fields that exist only for lazy wiring.
- [x] Construct one complete `TurnPreparationModule` in Daytona composition after storage and Skill catalog dependencies are available.
- [x] Update tests to construct the relevant provider or preparer directly; remove `object.__new__(LiveKernelResources)` setups.
- [x] Keep client construction, provider cleanup, engine disposal, orphan cleanup, and composition rollback behavior unchanged.

### Expected behavior

- Startup either installs one complete Daytona inventory or rolls it back.
- Turn preparation remains prepare-before-headers and uses the same deadline, Attachment staging, tools, Skill selection, Workspace capability, and Result Snapshot sink.
- Resource ownership and Turn preparation have separate, locally understandable interfaces.

### Focused validation

```bash
uv run pytest \
  tests/unit/backend/test_live_turn_preparation.py \
  tests/unit/backend/test_live_composition.py \
  tests/contracts/backend/test_skill_turn_contract.py \
  tests/contracts/backend/test_daytona_import_boundary.py -q
uv run ruff check src/fleet_rlm/daytona/run_environment.py src/fleet_rlm/composition/daytona.py tests
uv run ty check src/fleet_rlm
```

Expected: composition and preparation tests pass without lazy wiring or constructor bypasses.

---

## Task 4 - Extract the Session Workspace Agent

**Priority:** P1

**Files:**

- Add: `src/fleet_rlm/daytona/workspace_agent.py`
- Edit: `src/fleet_rlm/daytona/workspace_fs.py:1-636`
- Edit: `tests/unit/backend/files/test_workspace_fs.py`
- Edit: `tests/unit/backend/test_workspace_volume_gateway.py` only if its adapter fixture must load the packaged agent
- Check: `pyproject.toml` package discovery; no package-data entry should be needed for a normal Python module

### Target interface

`workspace_agent.py` is stdlib-only and exposes one pure entry function:

```python
def handle(request: Mapping[str, object]) -> dict[str, object]: ...
```

The request contains the operation, validated Volume root, Session Workspace root, normalized relative path, limits, write content, overwrite flag, and explicit unsupported errno allowlists. The response preserves the existing `ok`, `entry`, `entries`, `content`, `truncated`, `warnings`, `error`, and `errno` shapes.

The host adapter serializes one bounded request, executes the packaged source plus a minimal invocation inside the Sandbox, validates the bounded JSON response, and maps errors exactly as today.

### Code changes

- [x] Move descriptor-relative traversal, no-follow opening, list/stat/read/write, atomic publication, overwrite fallback, rollback, fsync, and response construction into `workspace_agent.py`.
- [x] Keep the remote module independent of `fleet_rlm` imports so it executes in a vanilla Daytona Sandbox.
- [x] Retain explicit Linux `ENOSYS=38` and `EOPNOTSUPP/ENOTSUP=95` handling.
- [x] Keep EPERM allowlisting scoped to the link operation and replacement errnos scoped to replacement.
- [x] Keep non-atomic overwrite and cleanup-failure warnings unchanged.
- [x] Reduce `_atomic_run()` to request construction, source invocation, response parsing, and error mapping.
- [x] Add direct local tests for `handle()` using temporary directories and controlled syscall failures.
- [x] Retain adapter-level tests that inspect the generated Sandbox request and public exceptions.

### Expected behavior

- Session Workspace list/stat/read/write semantics remain identical.
- Path traversal, symlink traversal, invalid UTF-8, oversized reads/writes, create conflicts, and directory/file mismatches continue to fail closed.
- The remote implementation can be reviewed and tested as Python rather than reconstructed from quoted strings.

### Focused validation

```bash
uv run pytest \
  tests/unit/backend/files/test_workspace_fs.py \
  tests/unit/backend/test_workspace_volume_gateway.py \
  tests/unit/backend/files/test_workspace_tools.py \
  tests/contracts/backend/test_workspace_turn_flow.py -q
uv run ruff check src/fleet_rlm/daytona/workspace_agent.py src/fleet_rlm/daytona/workspace_fs.py tests/unit/backend/files
uv run ty check src/fleet_rlm
```

Expected: all offline safety and behavior tests pass, and `_atomic_run()` is no longer a complexity hotspot.

---

## Task 5 - Centralize Turn Claim Transition Policy

**Priority:** P2

**Files:**

- Add: `src/fleet_rlm/chat/turn_claim.py`
- Edit: `src/fleet_rlm/chat/turn_lifecycle.py:78-212,339-364`
- Edit: `src/fleet_rlm/persistence/repositories/turns.py:48-365,553-783`
- Edit: `src/fleet_rlm/persistence/models.py` only if typing can be improved without a schema change; no migration is expected
- Add or edit: focused Turn Claim transition tests
- Retain and update: in-memory, SQL, heartbeat, cancellation, recovery, and coordinator tests

### Target domain types

```python
ClaimStatus = Literal["running", "settling", "completed", "failed", "cancelled", "timeout"]

@dataclass(frozen=True, slots=True)
class ClaimSnapshot:
    status: ClaimStatus
    held: bool
    failure: TurnFailure | None
    terminal_intent: Literal["failed", "cancelled", "timeout"] | None

class ClaimAction: ...
class FailClaim(ClaimAction): ...
class BeginSettlement(ClaimAction): ...
class RevokeClaim(ClaimAction): ...
class CompleteSettlement(ClaimAction): ...
class HeartbeatClaim(ClaimAction): ...

@dataclass(frozen=True, slots=True)
class ClaimDecision:
    status: ClaimStatus
    failure: TurnFailure | None
    terminal_intent: Literal["failed", "cancelled", "timeout"] | None
    release_claim: bool
    durable: bool

def decide_claim_transition(snapshot: ClaimSnapshot, action: ClaimAction) -> ClaimDecision: ...
```

Names may be adjusted to match repository terminology, but the module must remain dependency-light, pure, and independent of SQLAlchemy.

### Store seam

Reduce the state-store interface to:

```python
async def begin(request: BeginTurn) -> TurnStart: ...
async def commit(turn: ExecuteTurn, committed: CommittedTurn, artifacts: tuple[PromotedArtifact, ...]) -> CommittedTurnReceipt: ...
async def transition_claim(turn: ExecuteTurn, action: ClaimAction) -> FailedRunReceipt | None: ...
async def request_cancel(access: TurnAccess, run_id: UUID) -> CancelResult: ...
```

SQL and in-memory adapters map their storage state to `ClaimSnapshot`, call the pure policy, and apply the returned decision under their existing lock/transaction. Successful commit remains adapter-owned because it atomically updates Session history, checkpoint, Run, and Artifact metadata.

### Code changes

- [x] Write a complete table of legal and illegal transitions before editing adapters.
- [x] Implement the pure policy and typed decisions.
- [ ] Convert `TurnLifecycleModule` fail/settle/revoke/complete/heartbeat calls to `transition_claim()` actions.
- [x] Apply the same policy in `InMemoryTurnStateStore` and `SqlAlchemyTurnStateStore`.
- [x] Keep claim-token, ownership, Session access, and checkpoint checks in adapters before invoking the policy.
- [x] Reuse the same final-state decision during stale-claim recovery after provider fencing; keep recovery claiming and fencing orchestration in the SQL adapter.
- [x] Preserve idempotent replay and cancellation-request semantics.
- [ ] Delete duplicated failure-status/code decoding only when the shared policy fully owns the same validation.

### Expected behavior

- Both adapters accept and reject the same transition matrix.
- A successful terminal remains impossible before Turn Commit.
- Settling retains claim authority until owned cleanup completes.
- Claim loss revokes local `RunAuthority`, fences provider state, and terminates durably as `stale_claim`.
- Recovery retries failed fences without prematurely making the Run terminal.

### Focused validation

```bash
uv run pytest \
  tests/unit/backend/test_in_memory_turn_state.py \
  tests/unit/backend/test_sql_turn_state.py \
  tests/unit/backend/test_turn_lifecycle.py \
  tests/unit/backend/test_turn_lifecycle_cancellation.py \
  tests/unit/backend/test_turn_claim_heartbeat.py \
  tests/unit/backend/test_turn_coordinator_commit.py \
  tests/unit/backend/test_turn_coordinator_failures.py \
  tests/contracts/backend/test_run_cancellation_api.py -q
uv run ruff check src/fleet_rlm/chat src/fleet_rlm/persistence/repositories/turns.py tests/unit/backend
uv run ty check src/fleet_rlm
```

Expected: transition-table, adapter-parity, recovery, and coordinator behavior all pass.

---

## Task 6 - Refactor the RLM Runner Internals

**Priority:** P3

**Files:**

- Edit: `src/fleet_rlm/rlm/runner.py:62-493`
- Optionally add one private implementation module under `src/fleet_rlm/rlm/` only if it improves locality; do not create a new caller-facing interface
- Edit: `tests/unit/backend/rlm/test_runner_execution.py`
- Edit: `tests/unit/backend/rlm/test_runner_outcomes.py`
- Edit: `tests/unit/backend/rlm/test_runner_cancellation.py`
- Edit: `tests/unit/backend/rlm/test_trajectory_projection.py`
- Edit: `tests/contracts/backend/test_native_rlm_tracer.py`

### Target internal shape

- A private observation buffer owns execution details, relay overflow, capability drains, event recording, and trajectory reconciliation.
- A private worker monitor owns the worker task, pending relay read, cancellation probe, deadline polling, repeated caller cancellation settlement, and prediction/typed-stop result.
- `RLMRunner.stream(context)` owns high-level sequencing and outcome construction.
- `TurnEventStream` remains the only caller-visible stream interface.

### Code changes

- [x] Characterize current event order, overflow behavior, cancellation, timeout, worker settlement, trajectory upsert, and final reasoning deduplication before restructuring.
- [x] Consolidate repeated `details.append()` plus `recorder.record()` logic.
- [x] Move worker polling out of the nested generator's main success path.
- [x] Preserve non-cancellable worker ownership and `wait_owned()` behavior.
- [x] Preserve the interpreter and RLM observer hooks used for live per-iteration details.
- [x] Preserve native trajectory as the durable reconciliation source.
- [x] Preserve sanitized failure messages and observed usage semantics.
- [x] Keep terminal Runtime Events out of `RLMRunner`; the coordinator remains their owner.
- [x] Do not introduce an external executor, event bus, or generic pipeline abstraction.

### Expected behavior

- Live detail ordering, stable step identifiers, trajectory reconciliation, cancellation, timeout, and successful outcomes remain unchanged.
- `stream(context)` remains a deep interface with fewer responsibilities interleaved in one function.
- Changed runner entrypoints satisfy `C901` without suppressions.

### Focused validation

```bash
uv run pytest \
  tests/unit/backend/rlm/test_runner_execution.py \
  tests/unit/backend/rlm/test_runner_outcomes.py \
  tests/unit/backend/rlm/test_runner_cancellation.py \
  tests/unit/backend/rlm/test_trajectory_projection.py \
  tests/unit/backend/rlm/test_tool_observer.py \
  tests/contracts/backend/test_native_rlm_tracer.py \
  tests/contracts/backend/test_coordinator_runner_failures.py -q
uv run ruff check src/fleet_rlm/rlm/runner.py --select C901
uv run ruff check src/fleet_rlm/rlm tests/unit/backend/rlm tests/contracts/backend/test_native_rlm_tracer.py
uv run ty check src/fleet_rlm
```

Expected: runner behavior remains stable and changed runner functions no longer exceed the configured complexity threshold.

---

## Task 7 - Reconcile Documentation and Run the Full Exit Gate

**Priority:** P3

**Files:**

- Edit only documentation whose current architecture description becomes inaccurate.
- Do not edit generated API artifacts unless `make api-check` proves a contract change, which is not expected.
- Keep this plan unchanged unless the user explicitly requests a plan update.

### Documentation checks

- [x] Update `docs/reference/codebase-map.md` if Daytona preparation or the Turn Claim module changes ownership descriptions.
- [x] Update `src/fleet_rlm/AGENTS.md` only if the durable module ownership rules materially change.
- [x] Remove documentation references to deleted observability exporters or manager lifecycle methods if any are found.
- [x] Confirm no public API or generated-contract documentation changed.
- [x] Record the actual final code diff statistics and compare them with the forecast without turning estimates into success criteria.

### Full validation gate

```bash
make check
make test-deno
make check-security
make build-release
make check-release
git diff --check
```

- [x] Run every command above successfully.
- [x] Run `make api-check` explicitly if it is not visible in the `make check` output.
- [x] Confirm `git diff -- openapi.yaml tools/fleet-tui/src/generated/openapi.ts` is empty.
- [x] Confirm no direct `litellm` imports were introduced.
- [x] Confirm unrelated user changes remain preserved.

### Credentialed Daytona proof

When credentials and explicit live authorization are available:

```bash
FLEET_LIVE=1 uv run pytest tests/live/backend/test_fleet_rlm_daytona_mvp.py -q -n 0 --timeout=900
FLEET_LIVE=1 uv run pytest tests/live/backend/test_attachment_artifact_durability.py -q -n 0
```

- [ ] Verify Session Workspace create/read/overwrite and Sandbox replacement continuity against Daytona.
- [ ] Verify Attachment and Artifact durability and cleanup.
- [x] If live proof is unavailable, label it pending; do not replace it with an offline success claim.

## Final Acceptance Criteria

- [x] No production references remain to the removed Turn trace/export pipeline.
- [x] No test-only lifecycle methods remain on `DaytonaSessionManager`.
- [x] Turn orchestration uses declared protocols without `inspect.signature()` or optional Fleet-owned lifecycle method discovery.
- [x] `LiveKernelResources` has no lazy Turn preparation state or post-construction preparation configuration.
- [x] Tests no longer bypass `LiveKernelResources.__init__()` with `object.__new__`.
- [x] Session Workspace remote logic is a directly tested Python module, and `_atomic_run()` is a small host adapter.
- [x] In-memory and SQL Turn Claim adapters pass one shared transition matrix.
- [x] `RLMRunner.stream(context)` remains the sole execution interface and its changed entrypoints pass `C901`.
- [x] HTTP, Runtime Event, SSE, replay, `CommittedTurn`, database, Attachment, Artifact, Skill, Deno, and Daytona behavior remain compatible.
- [x] Generated OpenAPI and TUI HTTP types are unchanged.
- [x] The full non-live, Deno, security, build, release, and diff gates pass.
- [x] Credentialed Daytona proof passes at the candidate revision or is explicitly recorded as pending.
- [x] No unrelated user changes are reset, overwritten, staged, committed, or pushed.

## Assumptions

- `src/fleet_rlm` is an application-internal Python package; removing unused internal constructor arguments and manager methods is acceptable. Public HTTP and wire interfaces remain the compatibility boundary.
- Existing code, tests, generated contracts, and tracked architecture documentation override stale plan text.
- Exact implementation line counts and net diff size are unknown until implementation; success is based on behavior, interface depth, deleted dead paths, and validation rather than raw LOC reduction.
- No schema migration is required. If implementation reveals a schema need, stop and revise the plan before changing Alembic artifacts.
- No new provider calls, commits, pushes, deployments, or production mutations are authorized by this plan alone.
