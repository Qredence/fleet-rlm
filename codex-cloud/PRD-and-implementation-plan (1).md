# Fleet-RLM: a smaller Daytona interpreter boundary

**Review date:** 30 September 2026
**Repository baseline:** `Qredence/fleet-rlm` at `c3f3d536242c7663cfeecdfbf63670b51630e9aa`
**Verified dependency contract:** Fleet 0.7.10, DSPy 3.4.0, Daytona 0.218.0. [S1]

## 1. Decision and evidence limits

**Recommendation: retain native DSPy and Fleet's existing Daytona lifecycle; simplify the invocation-scoped interpreter and its existing broker in place. Do not introduce another provider framework or replace the execution transport in the same change.**

This is an independently researched recommendation against the pinned code, not a completed comparison of Plans A-D. The four exact attachment paths were attempted and were not readable. Their internal page links do not supply their contents. Consequently, no position, strength, weakness, or ranking is attributed to those plans.

Two historical attachments were available: `RLM Architecture Audit.txt` and `Branch - RLM Architecture Audit.txt` (the uploaded second filename uses a middle dot). They are useful project context, but are not substitutes for Plans A-D. The first proposes bounded child fan-out; the later one describes that fan-out as implemented. Their old caller-owned interpreter discussion must not override the current DSPy 3.4 factory contract.

The code review was focused on the Daytona interpreter, broker, relevant lifecycle definitions, factory call sites, and the exact DSPy interpreter lifecycle. It was not a complete audit of every repository file. Current source was read through the connected GitHub service. A full local clone was unavailable, and the execution environment had neither DSPy nor Daytona installed.

The supplied patch therefore implements **PR 1 only**: two concrete corrections and eight regression cases in an existing test file. The subsequent refactor is specified below, but has not been implemented or live-certified.

## 2. Why this is the appropriate interpretation of Monty

The Monty reference exposes a small DSPy-facing interface and uses a fresh interpreter factory for each invocation. Its README explicitly documents DSPy 3.4's factory ownership. This is the pattern to follow. Monty's restricted Python/runtime and filesystem model should not be copied into a full remote CPython sandbox. [S2]

The exact DSPy 3.4 source creates an interpreter, injects execution tools and output metadata, yields it to the RLM, and calls `shutdown()` in a `finally` block. Its protocol distinguishes recoverable `CodeExecutionError` from terminal `CodeInterpreterError`; the former subclasses the latter. [S3, S4]

Fleet already has `new_invocation()` and uses it from serving, recursive execution, optimization evaluation, and the OOLONG benchmark adapter. A factory migration is not a new feature to build from scratch. The maintainability task is to remove machinery that still assumes mutable, repeatedly rebound invocations while preserving supported standalone interpreter use. [S5, S6]

### Architecture choices

| Choice | Assessment | Decision |
|---|---|---|
| Split large files into many helpers without changing ownership | Moves complexity rather than removing it | Reject as the primary strategy |
| Replace the broker with a bare process/code execution call | Does not establish persistent state, host callbacks, structured final output, or containment parity | Reject as an unproven shortcut |
| Native Daytona interpreter contexts plus a callback-only transport | A plausible later simplification, but exact pinned-SDK behavior and cleanup parity remain unverified | Defer to a separate, bounded experiment |
| Invocation-scoped DSPy adapter over the existing execution path | Builds on the current contract and permits targeted deletion without simultaneous infrastructure replacement | Implement now |

This decision does not assert that Daytona's native interpreter is inadequate. Public documentation shows isolated interpreter contexts and output callbacks. It also explicitly warns that `request_timeout` on context operations is client-side and does not cancel the server operation. Those facts do not certify a replacement for Fleet's current 0.218.0 execution path. [S7]

## 3. Findings that change the implementation plan

### 3.1 Do not schedule an obsolete DSPy upgrade

The repository already pins DSPy 3.4.0 and Daytona 0.218.0. Leave the lockfile and dependency pins unchanged in this program. An older proposal to upgrade from DSPy 3.3.0 to 3.3.1 would be stale against this baseline. [S1]

### 3.2 The broker currently executes Python

`broker.py` explicitly owns the live invocation's executable namespace. Its sandbox-local HTTP server executes code, captures stdout/stderr, detects structured final output, queues host tool calls, and accepts correlated results. The host process polls and invokes registered tools rather than executing generated Python itself. [S8]

Removing that broker is therefore an execution-engine replacement, not just removing an HTTP helper. Keep one execution path in this program. Do not retain two engines indefinitely behind a feature flag.

### 3.3 A backend TypeError can replay an action

`DaytonaCodeInterpreter._run_backend()` currently invokes the backend with `on_stdout`, catches any `TypeError`, and then invokes the backend again without the keyword. That exception can occur inside a supported backend, or inside its output callback, after work has begun. [S5]

The correct rule is **select the call shape before executing it**. A failure after execution begins is not evidence that a different signature should be tried. The supplied patch uses signature binding for the existing legacy compatibility case and leaves backend invocation outside the handler.

The isolated checks reproduce both duplicate execution and a swallowed callback failure. They do not prove that a production customer experienced either defect.

### 3.4 The public-output helper checks an exception superclass first

`_public_output()` tests `CodeInterpreterError` before `CodeExecutionError`. The recoverable helper branch is unreachable because of the verified inheritance. Reorder those checks. [S3, S5]

This is a helper classification defect, not evidence that every recoverable RLM error aborts a Turn. The main `_execute_once()` exception handler already distinguishes the two categories. [S5]

### 3.5 Invocation ownership is not sandbox ownership

The current runtime documents reusable session roots and disposable child environments. `new_invocation()` creates fresh adapter/backend state without deleting the retained root sandbox. Keep that distinction. DSPy's finalization must not accidentally delete shared persistent storage or retire a healthy retained root sandbox. [S5, S9]

## 4. Product requirements document

### Product objective

Make Fleet's Daytona interpreter easy to understand and maintain as a DSPy adapter, while preserving native RLM behavior, host-tool authorization, useful observability, durable application state, and safe resource settlement.

### Users and expected behavior

A Fleet user submits a task through the existing UI or API. Python variables persist between actions of that task. A new Turn gets a fresh interpreter namespace. Approved workspace files, skills, and memories remain available through the existing scoped persistence mechanisms. Errors that generated code can repair return to the model; infrastructure or containment failures do not masquerade as successful results.

A maintainer should be able to locate the owner of each concern without tracing a registry or several interchangeable backend layers: DSPy owns reasoning, the adapter owns the invocation contract, the backend/broker owns execution, and the runtime owns the sandbox lease.

### In scope

Correct at-most-once local dispatch, simplify invocation binding, establish one internal execution-result shape, retain one broker owner, preserve tool and output semantics, and update existing contract tests and affected call sites. Refactor without changing the dependency pins or public API transport schemas.

### Out of scope

No custom RLM loop, provider registry, backend plugin framework, deeper recursion, new swarm scheduler, model selection/optimization program, frontend redesign, session database migration, new memory service, snapshot pooling system, or new configuration surface. Native-context migration is not bundled into this release. Existing optimization and benchmark callers receive compatibility updates only, not algorithmic changes.

### Functional requirements

| ID | Requirement | Acceptance condition |
|---|---|---|
| FR-01 | Keep native `dspy.RLM` as the cognitive engine | No replacement iteration loop or fork of DSPy internals |
| FR-02 | A fresh interpreter for every native invocation | Two invocations have different adapter and REPL state, even when their root sandbox is retained |
| FR-03 | Preserve state within an invocation | A variable created in action one is available in action two |
| FR-04 | Dispatch a backend action at most once locally | Backend/callback TypeError cannot trigger another backend call |
| FR-05 | Install invocation bindings once | DSPy tool/output injection completes before first execution; later mutation is rejected, not transparently rebound |
| FR-06 | Keep native semantic tools | `llm_query` and `llm_query_batched` remain DSPy-provided; Fleet does not duplicate them |
| FR-07 | Preserve authorized host callbacks | Sync and async tools use the existing authority/deadline bridge and registered tool allowlist |
| FR-08 | Use a single structured backend result | Final output is not inferred from arbitrary stdout; stdout remains data |
| FR-09 | Preserve repair versus terminal behavior | Recoverable code errors reach DSPy as repairable; protocol/state-loss/cleanup failures remain terminal |
| FR-10 | Preserve lifecycle and persistence boundaries | Interpreter shutdown does not delete a shared volume; child resources remain separately owned and settled |
| FR-11 | Preserve application validation and commit rules | A Prediction still passes Fleet's existing validation and authorized commit boundary |
| FR-12 | Preserve event contracts | Existing event identities, budgets, ordering, and bounded projections remain valid |

### Non-functional requirements

The accepted refactor adds no production module, dependency, or configuration field. It does not increase the number of test files. Complexity reduction is measured by removal of duplicated state and fallback paths, not by a line-count quota.

No generated Python runs in the production host process. The in-process backend remains an explicitly offline/testing capability, not a fallback when Daytona fails. The transport remains JSON-only with existing size limits and error sanitization. Ownership and deletion confirmation are not removed to make files shorter.

Reliability means that a timed-out request is not automatically treated as stopped execution. Do not permit another invocation to use a sandbox until the previous owner has been settled or the runtime has contained/retired the sandbox. An uncertain cleanup outcome cannot become a successful Turn.

## 5. Target ownership and execution design

| Owner | Owns | Must not own |
|---|---|---|
| Native DSPy RLM | Reasoning loop, native Sub-LM tools, interpreter creation/finalization, canonical trajectory | Fleet's session database or shared-volume lifecycle |
| `DaytonaCodeInterpreter` | DSPy protocol, one invocation's bindings, error/result translation, existing observation hooks | Sandbox creation/deletion policy or a second RLM loop |
| Existing sandbox backend | One broker reference, executing actions, translating transport responses, invocation teardown | Session retention and workspace policy |
| Existing HTTP broker | Sandbox execution namespace, bounded request/result transport, tool correlation | Arbitrary host Python evaluation or independent authorization policy |
| `DaytonaRuntime` | Root/child sandbox acquisition, scoped storage, admission, containment and lease settlement | DSPy reasoning or output synthesis |
| Existing Turn/application layer | Request authority, durable result commit, approved persistence effects | Unvalidated interpreter internals |

### Normal Turn

The Turn obtains its authorized runtime and root lease through the existing flow. Its zero-argument interpreter factory captures the current Run's observer, budget, context capsule, output contract, and async bridge, then calls `new_invocation()`.

DSPy constructs that invocation adapter and injects its native tools and output-field metadata. Fleet seals bindings immediately before the first actual `execute()`, including an execution used to prepare `SandboxSerializable` inputs. Sealing at constructor time or at `start()` is too early for the supported setup sequence. [S4]

Each action uses the same invocation namespace and a single backend dispatch. Registered host tools cross the existing broker and composition-loop bridge. The adapter returns bounded ordinary output, a recoverable exception, or `FinalOutput`. DSPy continues or finishes the native loop.

DSPy then finalizes its interpreter. Fleet's existing outer lifecycle continues to own outstanding workers, child resources, result validation, commits, and lease settlement. A factory-owned adapter is not an independently owned sandbox lease.

### Binding simplification

Retain the current `new_invocation()` seam; do not add a parallel `InterpreterFactoryService`. Use an ordinary tools dictionary for DSPy's pre-execution injection. At first execution, copy the tool-name/callable bindings and output metadata and install the backend bindings once.

For subsequent executes, compare names and callable identities against the sealed binding snapshot and compare output metadata against its stored copy. A changed binding is a configuration/lifecycle error before remote execution. Do not mutate a running broker to accommodate it.

Keep the execution single-flight guard and shutdown synchronization. Remove the generation/rebinding reservation machinery only after the invocation contract tests prove the sealed path works in both sync and async calls. In particular, eliminate redundant state such as desired/installed binding generations and pre-execution task reservation tokens, rather than simply moving it to a new module.

### Backend simplification

The final internal `InterpreterBackend.run` contract should accept the existing optional stdout callback and return `BackendExecutionResult` consistently. Update both real/offline implementations and test doubles together. Once that migration is complete, remove the legacy signature selection introduced conservatively by PR 1; a single typed call is the final target.

Keep one broker reference in the live backend. The adapter may expose a read-only forwarding property for existing inspection, but should not also own a second `_http_broker` cleanup branch. Update fake tests to inject the backend rather than fabricate another ownership model.

### Result simplification

Use `BackendExecutionResult` as the only result coming into the adapter. The sandbox-side SUBMIT helper reports a structured final payload through the existing execution response. Normal stdout, even text identical to an old sentinel, must never complete a Turn.

When all real and offline backends and test doubles use that shape, remove the legacy raw-string final-payload parsing path and any marker-only generation/projection code that no longer has a caller. Preserve the existing shared submit/output validators, bounded stdout/stderr, and public final-output label. Do not introduce another envelope class or validator hierarchy.

Do not delete a helper merely because its name contains 'legacy': every removal requires a reference scan and its behavioral acceptance test. This is a migration of a specific execution boundary, not a speculative file-pruning exercise.

## 6. Daytona storage and resource semantics

### Snapshots

For this program, keep Fleet's current configured root and child image/environment snapshots. They provide the execution environment, not a new application memory policy. Do not add snapshot-per-Turn, hot-snapshot, or fork-based continuation.

This is a Fleet design choice, not a claim that Daytona snapshots can never contain memory. Current Daytona documentation distinguishes image-built environments, cold filesystem snapshots, and hot VM snapshots. Those newer mechanisms are outside this pinned-runtime refactor. [S10]

### Retained root sandbox

A healthy root sandbox may outlive an individual Turn. That does not imply that the Python globals or broker process should survive the invocation. Reuse the sandbox according to current Fleet policy, but create a fresh invocation execution namespace and fresh bindings.

Namespace freshness is also not equivalent to filesystem freshness. Existing permitted local files may remain in a retained sandbox; durable state that must outlive sandbox retirement belongs in the approved persistent store. The refactor must not quietly widen mount permissions.

### Disposable children

Preserve the existing root/child runtime distinction and the current recursion semantics. Do not share a mutable REPL between child calls, and do not add another child scheduler. Child mounts and staging follow the existing semantic/workspace profile, not a new rule that every child sees the entire volume. [S9]

### Volumes and application state

Daytona documents volumes as shared, persistent FUSE-backed storage with scoped subpaths. Concurrent writes to the same path are not transactions. Retain application-level ownership of shared writes; this refactor must not assume that a mounted directory provides database locking. [S11]

Keep conversation/run records in Fleet's existing database arrangement. Keep valuable, explicitly promoted workspace knowledge in the established persistent filesystem layout. Do not move the session database into a shared volume or treat REPL globals as durable memory. Exact storage directories and schema remain unchanged.

Use the same volume identity and existing session/workspace prefixes. Invocation cleanup removes invocation-owned scratch and processes, not the volume itself. Root retirement and disposable child deletion are runtime operations with their existing confirmation requirements.

## 7. Failure behavior

| Situation | Required behavior |
|---|---|
| Syntax/runtime error in otherwise healthy generated code | Preserve the recoverable DSPy error path and bounded repair feedback |
| Backend TypeError after work begins | Report once; do not replay the action using another signature |
| Tool implementation failure | Preserve correlated, bounded tool error behavior; do not blindly retry a side-effecting tool |
| Tool result delivery fails after the tool ran | Preserve uncertainty and existing settlement rules; never infer that the tool did not run |
| Code exceeds an execution deadline | Fail the invocation and retain containment/cleanup ownership; a client timeout alone is insufficient proof of stop |
| REPL process or protocol state is lost | Terminal invocation failure; no silent restart that pretends variables survived |
| Binding changes after the first execute | Reject before another remote action |
| Invalid or oversized submitted output | Preserve existing repair/validation limits; no durable successful commit of invalid output |
| Invocation or child cleanup cannot be confirmed | Keep the failure visible to the runtime; do not return an apparently clean lease |

These rules preserve the distinction between reasoning failures the model can repair and infrastructure failures it cannot.

## 8. Implementation sequence

### PR 1 - prevent replay and correct helper classification (supplied)

**Files:** `src/fleet_rlm/daytona/interpreter.py` and `tests/unit/backend/daytona/test_daytona_adapter.py`.

Move signature compatibility checking before backend invocation. Validate the legacy shape before invoking that shape too. Do not catch a `TypeError` thrown by the backend itself as a compatibility signal. Test streamed and legacy backends, arbitrary keyword support, incompatible signatures, backend failure, and callback failure.

Check `CodeExecutionError` before its superclass in `_public_output()`. Test both categories without exposing private error details.

No dependency change, transport change, new module, or new test file. This is a self-contained correctness patch, not the entire simplification program.

### PR 2 - make invocation bindings genuinely invocation-scoped

**Primary owner:** `daytona/interpreter.py`. **Integration owners:** the existing root, child, optimization and benchmark factories listed below.

Keep `new_invocation()` and make it the only production path for creating the per-invocation adapter. Ensure each closure passes the current Run's values to the fresh adapter rather than mutating the retained template. Preserve factory execution instructions.

Replace refresh generations and task-local injection reservations with pre-execution configuration and one sealed-binding snapshot. Freeze immediately before first execute, not in `start()`. Derive the observed host-tool bindings once per invocation instead of rebuilding a new wrapper map for every action.

Preserve start idempotence, execute single-flight protection, async tool dispatch on the composition-owned loop, terminal state-loss behavior, callback nesting, and retryable cleanup accounting. Do not convert the whole application to sync or async APIs as part of this PR.

**Acceptance:** deterministic sync and async native-DSPy tests prove fresh invocations, same-invocation variable persistence, no observer/tool leakage across retained-root Turns, rejected post-seal mutations, and proper shutdown for failure during input setup as well as during generated-code execution.

### PR 3 - remove duplicate backend and result paths

**Primary owners:** `daytona/interpreter.py` and `daytona/broker.py`.

Standardize the internal backend callback/result contract and migrate existing test doubles. Then remove PR 1's temporary legacy signature branch. Give the live backend sole ownership of the broker reference and shutdown path.

Use structured final results end to end and remove now-unreferenced raw-string final detection. Preserve shared submit validation rather than reimplementing field rules in the broker. Keep stdout ordinary bounded data and preserve the existing final event projection.

Verify constructor configuration reaches the actual live backend. In the reviewed code, the live backend constructs the broker with `DEFAULT_BROKER_PORT` while the adapter stores a `broker_port` argument. Make the existing supported nonzero port effective rather than adding a second setting or transport mode; preserve the current rejection of brokerless host-tool dispatch. [S5]

**Acceptance:** existing broker/tool tests and the native vertical slice prove registered sync/async tools, stale/duplicate result handling, strict JSON/size limits, final-output validation, ordinary sentinel-looking stdout, and propagation of delivery/cleanup failures. The result is one execution backend path, one broker owner, and one backend result shape.

### Release gate

After the pinned dependency suite passes, run a live retained-root two-Turn scenario and the existing disposable-child and cancellation scenarios. Verify absence/containment using the existing runtime confirmation mechanisms, not just a returned delete request. A broker teardown failure must prevent a retained sandbox from being handed to the next invocation.

Merge each PR independently. No new permanent feature flag. Revert the affected PR on contract regression; no persistence migration is required by this plan.

## 9. Complete intended tree and integration owners

The following is the **entire intended Daytona package subtree**, not a partial or invented full-repository tree. It retains the seven existing files; this program adds none. [S12]

```text
src/fleet_rlm/daytona/
|-- __init__.py
|-- broker.py
|-- diagnostics.py
|-- errors.py
|-- interpreter.py
|-- runtime.py
`-- snapshot-requirements.txt
```

| File | Final responsibility / change |
|---|---|
| `__init__.py` | Preserve exports; no new facade |
| `broker.py` | Existing sandbox execution/host-tool transport, with one owner and one structured result path |
| `diagnostics.py` | Preserve existing health/diagnostic behavior; update a reference only when a removed private seam requires it |
| `errors.py` | Preserve provider/error categories; no new error framework |
| `interpreter.py` | Smaller invocation adapter, sealed bindings, one backend call, structured result mapping |
| `runtime.py` | Preserve acquisition, snapshots, mounts, admission, root/child lifecycles and cleanup confirmation |
| `snapshot-requirements.txt` | No planned change |

All known factory integration owners, keeping their existing paths, are:

```text
src/fleet_rlm/rlm/execution.py
src/fleet_rlm/rlm/recursion.py
src/fleet_rlm/rlm/program.py
src/fleet_rlm/optimization/daytona.py
src/fleet_rlm/optimization/routing.py
scripts/benchmarks/oolong/adapter.py
```

These are call-site/update owners, not six new abstractions. Existing tests should be extended in place. Known directly relevant existing files include:

```text
tests/unit/backend/daytona/test_daytona_adapter.py
tests/unit/backend/daytona/test_sandbox_variable_binding.py
tests/unit/backend/daytona/test_optimization_evaluator.py
tests/unit/backend/rlm/test_dspy_compat_seam.py
tests/unit/backend/rlm/test_recursion_lease_cleanup.py
tests/unit/optimization/test_routing_eval.py
tests/unit/scripts/test_run_oolong_predict.py
tests/contracts/backend/test_native_dspy_fastapi_vertical_slice.py
```

A full reference scan in the real checkout is required before removing private helpers. This inventory names the reviewed integration owners; it does not falsely claim to enumerate every reference in an unavailable checkout.

## 10. Test strategy and verification status

### Tests provided by PR 1

Four test functions, parametrized into eight cases, are inserted into the existing adapter test module. They cover backend-origin TypeError, stdout-callback TypeError, legacy dispatch, streaming dispatch, arbitrary keyword dispatch, incompatible signature rejection, and the two public error categories.

The accompanying isolated harness compiles the exact changed method bodies into a minimal adapter and supplies explicit surrounding stand-ins. It does not import or execute the full Fleet, DSPy, or Daytona stack.

| Check | Result in this session |
|---|---|
| Original method bodies against the new isolated cases | 3 failed, 5 passed; the intended defects reproduced |
| Patched method bodies against the same cases | 8 passed |
| Unified diff syntax / application against source-fragment fixtures | Passed |
| `git apply --check` against a full actual repository checkout | Not run |
| Existing Fleet test suite with pinned dependencies | Not run |
| Ruff / type checker for the actual repository | Not run |
| Live Daytona/model/cancellation tests | Not run |

Do not call the patch production-certified on this evidence. The source-fragment check proves hunk integrity, not whole-repository compatibility. The public-output fix is specifically a helper-level correction.

### Tests for the complete program

Keep tests at the established owner boundaries. Use deterministic LMs for native factory/binding behavior. Keep one existing broker-contract location for request correlation and result semantics. Reuse the current live lifecycle and vertical-slice lanes rather than adding a parallel suite of thin mock modules.

Essential live acceptance combines fresh REPL state across two Turns on a retained root, persistent approved file access, sync/async host callbacks, disposable child isolation, cancellation during execution and tool calls, and confirmed lease cleanup. Retain the existing non-live tests for public events and durable result validation.

## 11. Applying the supplied patch

Work from a clean checkout of the reviewed base. Save the patch outside the repository or substitute its actual path in these commands:

```bash
git switch -c fix/daytona-dispatch c3f3d536242c7663cfeecdfbf63670b51630e9aa
git apply --check /absolute/path/fleet-daytona-correctness.patch
git apply /absolute/path/fleet-daytona-correctness.patch
uv sync --frozen --dev
uv run pytest tests/unit/backend/daytona/test_daytona_adapter.py
uv run ruff check src/fleet_rlm/daytona/interpreter.py tests/unit/backend/daytona/test_daytona_adapter.py
uv run ruff format --check src/fleet_rlm/daytona/interpreter.py tests/unit/backend/daytona/test_daytona_adapter.py
uv run ty check
git diff --check
```

Those repository commands are the required next verification, not commands claimed to have passed here. Review the diff and execute the project's wider established checks before merging. The bundle's `manifest.json` records both original file blob hashes and the patch SHA-256.

`replacement-methods.py` contains complete replacement method bodies for review, **not a replacement for the whole interpreter file**. `regression-tests.py` contains the complete added tests, **not a replacement for the whole existing test module**. Apply the unified patch rather than overwriting either repository file with those review excerpts.

## 12. Definition of done and expected benefit

The program is complete when native DSPy still owns the algorithm and invocation finalization; root/child sandbox ownership and storage scope are unchanged; invocation bindings have one installation path; backend dispatch is single-call and typed; the live broker has one owner; final output has one structured transport; and pinned offline plus live lifecycle checks pass.

The expected benefit is lower maintenance risk and clearer execution ownership, not a claimed latency or token-cost improvement. No benchmark in this review establishes performance gains. The small supplied patch prevents a concrete class of duplicate local dispatch and corrects a helper's exception classification. PRs 2-3 remove the larger architectural duplication without adding another runtime framework.

A final Plan A-D selection remains pending readable exports of those four documents. This recommendation should be compared with them when available, not retroactively presented as a review of their contents.

## Source register

All repository sources below were read at the stated ref. Current public documentation is descriptive evidence, not a substitute for testing the pinned SDK.

- **S1:** Fleet dependency contract: https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/pyproject.toml
- **S2:** Monty reference README, retrieved 30 September 2026; blob `33da4a1a9f741e5c2e26e986cce1045043019e86`: https://github.com/dbreunig/dspy-monty-interpreter/blob/main/README.md
- **S3:** DSPy 3.4.0 interpreter protocol and exceptions: https://github.com/stanfordnlp/dspy/blob/3.4.0/dspy/primitives/code_interpreter.py
- **S4:** DSPy 3.4.0 RLM, especially lines 390-680: https://github.com/stanfordnlp/dspy/blob/3.4.0/dspy/predict/rlm.py
- **S5:** Fleet interpreter, selected reviewed slices within lines 1-1510 (not the whole module): https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/src/fleet_rlm/daytona/interpreter.py
- **S6:** Factory callers discovered by code search at the pinned Fleet ref: `rlm/execution.py`, `rlm/recursion.py`, `optimization/daytona.py`, `optimization/routing.py`, and `scripts/benchmarks/oolong/adapter.py`.
- **S7:** Daytona async interpreter documentation: https://www.daytona.io/docs/en/python-sdk/async/async-code-interpreter/
- **S8:** Fleet broker: https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/src/fleet_rlm/daytona/broker.py
- **S9:** Fleet runtime, reviewed opening definitions: https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/src/fleet_rlm/daytona/runtime.py
- **S10:** Daytona snapshots and persistence: https://www.daytona.io/docs/snapshots/ and https://www.daytona.io/docs/en/persistence/
- **S11:** Daytona volumes and concurrent writes: https://www.daytona.io/docs/volumes/
- **S12:** Full Daytona package directory listing at the pinned ref: https://github.com/Qredence/fleet-rlm/tree/c3f3d536242c7663cfeecdfbf63670b51630e9aa/src/fleet_rlm/daytona
- **S13:** Existing adapter tests, reviewed opening 220 lines: https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/tests/unit/backend/daytona/test_daytona_adapter.py
- **S14:** User-provided historical attachments: `RLM Architecture Audit.txt` and `Branch - RLM Architecture Audit.txt`; read as historical background only, not as Plans A-D.
