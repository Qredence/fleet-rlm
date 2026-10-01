# Fleet-RLM: smaller Daytona interpreter boundary

**Document type:** normative PRD + implementation plan
**Review date:** 2026-09-30
**Repository baseline:** `Qredence/fleet-rlm@c3f3d536242c7663cfeecdfbf63670b51630e9aa`
**Runtime baseline:** Fleet 0.7.10, DSPy 3.4.0, Daytona 0.218.0
**Companion evidence:** `BASELINE-AND-EVIDENCE.md`
**Decision record:** `DECISION-RECORD.md`
**Execution checklist:** `IMPLEMENTATION-CHECKLIST.md`

> This document is self-contained and normative. No prior architecture-plan document is required to interpret, implement, test, or review it.

## 1. Executive decision

Retain **native `dspy.RLM`**, Fleet's existing **Daytona Sandbox lifecycle**, and the existing **broker-backed execution path**. Simplify the interpreter **in place** around DSPy 3.4's invocation-scoped factory contract.

The target is deliberately narrow:

```text
DSPy owns the reasoning loop and one interpreter invocation.
Fleet owns application correctness and Sandbox resources.
DaytonaCodeInterpreter is the thin seam between them.
```

The implementation program has three PRs:

1. fix two concrete adapter correctness issues;
2. replace repeatable mutable rebinding with invocation-lifetime binding;
3. standardize one backend protocol/result shape and one broker owner.

Do **not** add a provider registry, second RLM loop, generalized RPC framework, new execution engine, new production module, new dependency, or permanent feature flag.

## 2. Problem statement

Fleet already follows the correct high-level architecture: it uses native DSPy, executes model-authored Python in Daytona, mediates host tools, separates retained root Sandboxes from disposable children, and keeps durable application state outside the interpreter namespace.

The maintainability problem is inside the adapter seam. `DaytonaCodeInterpreter` still contains compatibility paths and rebinding machinery that are broader than the current DSPy 3.4 ownership model requires.

The concrete issues are:

- `_run_backend()` can execute a backend twice when a `TypeError` occurs after execution has begun;
- `_public_output()` checks `CodeInterpreterError` before its `CodeExecutionError` subclass;
- tool/output bindings are tracked through generations plus async reservation state even though production creates a fresh invocation adapter;
- `_finalize()` still accepts both a structured backend result and a legacy raw string;
- broker ownership/configuration is represented in more than one layer.

The objective is not simply fewer lines. It is **fewer owners, fewer mutable states, and fewer compatibility branches** while retaining the safety behavior that Daytona requires.

## 3. Design principles

### 3.1 Native DSPy remains the cognitive engine

Fleet must not own a replacement loop for:

```text
reason -> generate Python -> execute -> observe -> repeat -> SUBMIT
```

DSPy owns the RLM algorithm, interpreter factory invocation, native semantic tools, output-field injection, and interpreter finalization.

### 3.2 The Monty reference is a lifecycle north star, not a runtime blueprint

The useful Monty pattern is smallness at the DSPy boundary:

```text
factory -> fresh interpreter -> execute -> shutdown
```

Fleet should mirror that ownership shape.

Fleet should **not** copy Monty's restricted local runtime semantics because Daytona is a remote CPython execution system with:

```text
Sandbox lifecycle
Snapshots
Volumes
retained session roots
disposable recursive children
host tool mediation
async SDK bridging
confirmed cleanup/containment
```

### 3.3 One concern, one owner

| Concern | Owner |
|---|---|
| RLM reasoning/iteration | DSPy `dspy.RLM` |
| Per-invocation interpreter adaptation | `DaytonaCodeInterpreter` |
| Current sandbox execution namespace | `_SandboxProcessBackend` + `DaytonaHttpToolBroker` |
| Host-tool correlation/transport | `DaytonaHttpToolBroker` |
| Sandbox acquisition/retention/deletion | `DaytonaRuntime` |
| Volume/mount scope | `DaytonaRuntime` / existing binding utilities |
| Turn authorization and durable state | existing Fleet application layer |
| Public result/event contracts | existing Fleet result/event layers |

### 3.4 Simplify before replacing

A Daytona native-interpreter-context implementation may eventually be smaller. It is not bundled here because replacing the broker would also replace state persistence, host callbacks, final-output transport, and containment behavior. That deserves an isolated parity experiment after this program.

## 4. Runtime model after the refactor

### 4.1 Retained root Sandbox, fresh invocation namespace

The expected lifecycle is:

```text
Fleet session/root Sandbox lease
        |
        +-- Turn A
        |     DSPy factory()
        |       -> fresh DaytonaCodeInterpreter
        |       -> fresh backend/broker namespace
        |       -> action 1
        |       -> action 2 (same Python globals)
        |       -> SUBMIT
        |       -> DSPy shutdown of invocation adapter
        |
        +-- root Sandbox remains owned by Fleet if policy retains it
        |
        +-- Turn B
              DSPy factory()
                -> new DaytonaCodeInterpreter
                -> new backend/broker namespace
                -> no Python globals from Turn A
```

Retaining a Sandbox is **not** permission to retain arbitrary REPL globals across Turns.

### 4.2 Filesystem persistence is separate from interpreter state

Approved workspace or memory data may persist according to the existing Volume/workspace rules. That persistence is not implemented by keeping one Python namespace alive across Turns.

No part of this refactor moves the session database into a shared Volume or changes the existing durable persistence model.

### 4.3 Child execution remains isolated

Recursive/disposable children keep their existing Sandbox ownership and cleanup rules. A child interpreter is fresh, a child Sandbox remains separately owned, and child teardown is not weakened to make the adapter smaller.

## 5. Product requirements

### 5.1 Functional requirements

| ID | Requirement | Acceptance condition |
|---|---|---|
| FR-01 | Preserve native DSPy | No custom RLM loop or DSPy fork |
| FR-02 | Fresh interpreter per native invocation | Separate adapter + executable namespace per Turn/child invocation |
| FR-03 | Preserve same-invocation state | Python variables defined in action N are available in action N+1 |
| FR-04 | At-most-once local backend dispatch | A backend or stdout callback exception cannot cause an automatic second backend call |
| FR-05 | Invocation-lifetime bindings | DSPy injection is allowed before first execution; later mutation is rejected instead of rebound |
| FR-06 | Preserve native DSPy semantic tools | Do not recreate `llm_query` / `llm_query_batched` as Fleet-specific equivalents |
| FR-07 | Preserve authorized host tools | Only registered host tools are reachable; async tools use the existing bridge/deadline semantics |
| FR-08 | One structured internal backend result | First-party backend `run()` returns `BackendExecutionResult` |
| FR-09 | Structured final output | `SUBMIT` final data does not depend on parsing arbitrary stdout |
| FR-10 | Correct repair/terminal behavior | `CodeExecutionError` remains repairable; terminal interpreter/protocol failures terminate |
| FR-11 | One live broker owner | Broker lifecycle belongs to the live backend; adapter does not independently own a competing teardown path |
| FR-12 | Preserve resource ownership | DSPy shutdown of an invocation does not delete shared Volume state or bypass Fleet Sandbox lease settlement |
| FR-13 | Preserve existing output/event budgets | No increase in unbounded stdout, trace, tool, or final-output surfaces |
| FR-14 | Preserve application contracts | No intentional OpenAPI/SSE/durable-result schema change |

### 5.2 Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | Add no production module in this program |
| NFR-02 | Add no dependency |
| NFR-03 | Add no permanent feature flag |
| NFR-04 | Prefer existing test files; no new thin test suite |
| NFR-05 | No generated Python executes in the production Fleet host process |
| NFR-06 | Cleanup failure remains observable and safety-relevant |
| NFR-07 | Keep public error text bounded/sanitized |
| NFR-08 | No claimed latency/token improvement without benchmark evidence |

## 6. Non-goals

The following are explicitly outside this implementation program:

```text
Daytona native interpreter-context migration
new broker protocol
new network transport
deeper recursive RLM depth
new child fan-out behavior
memory redesign
session database redesign
new snapshot pooling/hot continuation system
model routing/GEPA redesign
TUI/frontend redesign
large unrelated source-tree reorganization
```

## 7. Detailed target design

### 7.1 Interpreter construction

Keep `DaytonaCodeInterpreter.new_invocation(...)`.

The retained template contains reusable configuration and a reference to the owned Sandbox backend/template. A production factory closure calls `new_invocation()` for the current Run and supplies invocation-local state.

Do not add a new factory class unless a concrete call site proves a closure cannot express the contract.

### 7.2 Binding phase

DSPy 3.4 legitimately mutates the interpreter before execution by:

```text
interpreter.tools.update(execution_tools)
interpreter.output_fields = ...
```

Fleet also binds its invocation-local observer/budget/context/bridge state.

Therefore the lifecycle must be:

```text
create fresh adapter
-> configure invocation state
-> DSPy injects tools/output fields
-> first execute begins
-> build/install backend bindings once
-> seal binding state
-> execute remote code
-> later executes reuse installed bindings
-> shutdown
```

Do **not** seal at object construction. Do **not** seal prematurely in `start()` if setup/injection can still legally occur before first execute.

After first execution starts, mutation is a misuse. Reject it before another backend call instead of refreshing backend bindings.

### 7.3 Concurrency

Removing binding reservations does **not** mean removing execution protection.

Keep a simple nonblocking single-flight guard for `execute()`. If two callers try to execute the same invocation adapter concurrently, the second fails closed with the existing reuse/lifecycle error semantics.

Invocation isolation should come from the factory. Single-flight remains a defensive invariant for standalone use and accidental reuse.

### 7.4 Tool binding

Before first remote execution:

1. copy the invocation's current tool map;
2. wrap only the tools that require Fleet observation/projection;
3. create host-dispatch wrappers preserving call signatures where possible;
4. bind that map into the backend once;
5. retain it as the invocation's `_bound_tools` for `invoke_tool()`.

Do not rebuild the wrapper map on every action.

### 7.5 Output contract binding

Merge the Fleet output contract and DSPy output fields before first execution. Install the resulting output metadata and max final-output size into the backend once.

After sealing, output-field mutation is rejected.

### 7.6 Context binding

Attachment/context capsule integrity bindings stay invocation-local and immutable once installed. Preserve existing checksum and trusted-mount-root verification.

### 7.7 Backend protocol

After PR 3 the protocol is exactly:

```python
class InterpreterBackend(Protocol):
    def run(
        self,
        code: str,
        variables: dict[str, object] | None = None,
        *,
        on_stdout: OutputCallback | None = None,
    ) -> BackendExecutionResult: ...

    def close(self) -> None: ...
```

No `str | BackendExecutionResult` union remains internally.

### 7.8 Result handling

`DaytonaCodeInterpreter._finalize()` receives `BackendExecutionResult` only.

```text
raw.error
  -> sanitize/categorize
  -> repair feedback OR terminal interpreter error

raw.final
  -> dspy.FinalOutput

otherwise
  -> bounded stdout
```

Ordinary stdout is never interpreted as a final result merely because it contains a marker-looking string.

### 7.9 Broker ownership

`_SandboxProcessBackend` owns its `DaytonaHttpToolBroker`.

The adapter may expose an inspection property that delegates to the backend for tests/diagnostics, but shutdown must not have two independent broker owners.

### 7.10 Broker port

The existing adapter constructor has `broker_port`. PR 3 must make that value effective in `_SandboxProcessBackend` or remove it after a repository-wide usage scan. The preferred minimal behavior is to pass the configured non-zero value through to the backend.

No additional port/config setting is introduced.

## 8. Implementation plan

## PR 1 — correctness hardening

**Patch included:** `fleet-daytona-correctness.patch`

### Change 1: prevent TypeError-triggered replay

Current risk:

```python
try:
    return run(code, variables, on_stdout=on_stdout)
except TypeError:
    return run(code, variables)
```

Target behavior for the temporary compatibility period:

```python
signature = inspect.signature(run)
try:
    signature.bind(code, variables, on_stdout=on_stdout)
except TypeError:
    signature.bind(code, variables)
    return run(code, variables)
return run(code, variables, on_stdout=on_stdout)
```

The important rule is that `TypeError` from **signature binding** may select the old shape; `TypeError` from **execution** may not trigger a retry.

### Change 2: correct public error classification

Check the subclass first:

```python
if isinstance(result, CodeExecutionError):
    return "Execution error"
if isinstance(result, CodeInterpreterError):
    return "Execution failed"
```

### Tests

Add tests to the existing `test_daytona_adapter.py` only:

```text
backend raises TypeError after call begins -> one call
stdout callback raises TypeError -> one call
legacy backend -> selected before execution
streaming backend -> on_stdout passed
variadic backend -> on_stdout passed
incompatible backend -> rejected before execution
CodeExecutionError -> Execution error
CodeInterpreterError -> Execution failed
```

### PR 1 scope guard

No lifecycle refactor, no dependency change, no broker rewrite.

## PR 2 — invocation-scoped binding simplification

### Remove/reduce

After a real checkout reference scan, remove machinery whose only job is repeated rebinding/async pre-execution reservation:

```text
_BindingTools generation bookkeeping
_binding_generation
_installed_binding_generation
needs_binding_refresh(...)
_BindingTools mutation generation bumps
_BindingTools-triggered task reservation
_BINDING_RESERVATION contextvar
_reservation_token
_reservation_task
_execution_started reservation state
_reservation_state_lock where no longer required
_begin_binding_injection()
_release_reservation()
```

The final exact deletion list depends on repository-wide references and tests. Do not delete by name without that scan.

### Keep

```text
new_invocation()
execution single-flight lock
shutdown lock
observer/output streaming
Turn budget
async bridge
context integrity binding
run scratch binding/cleanup
output contract merge
callback decorators
strict cleanup propagation
```

### Replace with

A small binding state model:

```text
configured (mutable before execution)
sealed     (immutable after first execute begins)
shutdown
```

Implementation can be a boolean/enum plus one installation method; do not create a state-machine framework.

Conceptually:

```python
def _install_bindings_once(self) -> None:
    if self._bindings_sealed:
        return

    tools = self._execution_tools()
    self._bound_tools = tools
    backend.bind_host_tools(_host_bindings(tools))
    backend.ensure_submit(self._output_fields, self._max_final_output_chars)
    if self._context_binding is not None:
        backend.bind_context_manifest(...)

    self._bindings_sealed = True
```

Mutation helpers check `_bindings_sealed` and reject later changes.

This pseudocode describes the intended ownership, not a mandatory exact implementation.

### PR 2 acceptance

- same adapter: variable persistence across two execute calls;
- new invocation: no variable leakage;
- retained template: no per-Run observer/budget/request mutation;
- DSPy tool/output injection works before first execute;
- post-seal tool/output/context mutation fails before backend execution;
- concurrent execute on one adapter fails closed;
- setup failure still leads to DSPy-owned shutdown;
- async tool bridge still works on the intended loop/deadline.

## PR 3 — protocol/result/broker unification

### Standardize backend `run()`

Make every first-party backend and test double accept `on_stdout` and return `BackendExecutionResult`.

Then simplify `_run_backend()` to one call:

```python
return backend.run(code, variables, on_stdout=on_stdout)
```

Delete PR 1's compatibility inspection once all internal implementations conform.

### Standardize `_finalize()`

Remove raw-string final detection from the interpreter boundary when search proves no supported caller still uses it.

### Consolidate broker ownership

The live backend creates, owns, and stops the broker. Adapter shutdown closes the backend; it does not separately race another broker owner.

### Propagate broker port

Pass the adapter's supported non-zero port to the live backend/broker. Do not add a second config surface.

### PR 3 acceptance

- ordinary stdout remains output;
- `SUBMIT` returns structured `FinalOutput`;
- sync and async host tools function;
- unknown tools fail closed;
- stale/duplicate broker result handling remains enforced;
- result size/JSON validation remains enforced;
- delivery failures propagate;
- broker cleanup failure propagates;
- fresh invocation gets a fresh namespace;
- no string result path remains unless a repository-wide search proves it is still required.

## 9. File-by-file change map

### `src/fleet_rlm/daytona/interpreter.py`

PR 1:

```text
_public_output
_run_backend
```

PR 2:

```text
_BindingTools / binding mutation behavior
constructor binding/reservation fields
new_invocation verification only (keep API)
mutation guards
execute single-flight simplification
_ensure_bindings -> install-once semantics
shutdown reservation cleanup removal if obsolete
```

PR 3:

```text
InterpreterBackend.run protocol
_run_backend direct typed call
_finalize typed-only path
broker inspection/ownership cleanup
broker_port propagation into backend construction
```

### `src/fleet_rlm/daytona/broker.py`

No redesign. Only changes necessary for one explicit owner/result contract or constructor propagation. Preserve correlation, request bounds, registered-tool allowlist, delivery-failure behavior, and strict stop semantics.

### `src/fleet_rlm/daytona/runtime.py`

No ownership redesign. Only adapt constructor/factory wiring if required by the simplified interpreter/backend signatures. Preserve roots, children, Volume mounts, admission, cleanup confirmation, and containment.

### `src/fleet_rlm/daytona/errors.py`

No new hierarchy. Reuse existing bounded/sanitized error mapping.

### Integration owners

Update only when required by the new invocation/backend signatures:

```text
src/fleet_rlm/rlm/execution.py
src/fleet_rlm/rlm/recursion.py
src/fleet_rlm/optimization/daytona.py
src/fleet_rlm/optimization/routing.py
scripts/benchmarks/oolong/adapter.py
```

Do not move their algorithms into the Daytona package.

## 10. Complete intended Daytona tree

The whole intended subtree remains:

```text
src/fleet_rlm/daytona/
├── __init__.py
├── broker.py
├── diagnostics.py
├── errors.py
├── interpreter.py
├── runtime.py
└── snapshot-requirements.txt
```

**Production files added by this program: 0.**

## 11. Test plan

Prefer behavioral contracts over tests of private implementation details.

### Unit/contract coverage

| Behavior | Existing owner to extend |
|---|---|
| adapter execute/lifecycle/error mapping | `test_daytona_adapter.py` |
| fresh invocation vs same-invocation variables | `test_sandbox_variable_binding.py` |
| DSPy factory contract | `test_dspy_compat_seam.py` |
| child lease cleanup | `test_recursion_lease_cleanup.py` |
| optimization factory | `test_optimization_evaluator.py` / `test_routing_eval.py` |
| benchmark adapter factory | `test_run_oolong_predict.py` |
| end-to-end native contract | `test_native_dspy_fastapi_vertical_slice.py` |

### Live acceptance

At minimum run the existing live lanes that prove:

```text
retained root reused across two Turns
fresh Python namespace for Turn 2
approved persistent workspace data still accessible
sync host tool dispatch
async host tool dispatch
child Sandbox isolation
cancellation during execution/tool wait
strict cleanup failure handling
confirmed child deletion/absence
no reuse while previous owned execution may still be active
```

## 12. Rollout and rollback

Merge the PRs independently.

### PR 1 rollback

Revert the patch. No persistent data migration exists.

### PR 2 rollback

Revert binding simplification if DSPy factory/setup/concurrency contracts regress. No public schema or persistence migration should be coupled to this PR.

### PR 3 rollback

Revert result/broker consolidation if a supported backend/test path was missed. Do not keep a permanent dual path as the rollback mechanism.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Seal bindings before DSPy finishes legitimate injection | Seal on first execute, not construction |
| Remove reservation logic needed by a standalone concurrency case | Keep direct execute single-flight test; scan all uses before deletion |
| Break child/optimization factories | Extend their existing tests; keep `new_invocation()` API |
| Treat client timeout as server cancellation | Keep runtime containment/cleanup semantics unchanged |
| Lose host-tool observation wrappers | Build observed tool map once before seal and test native semantic/host tools |
| Accidentally weaken SUBMIT validation | Reuse existing output contract/validation; only simplify transport shape |
| Make broker cleanup best-effort | Preserve strict propagation and safe lease reuse rules |
| Over-refactor while touching large files | Three bounded PRs; no unrelated file-tree cleanup |

## 14. Definition of done

The program is complete when all statements below are true:

```text
native dspy.RLM still owns the reasoning loop
one fresh interpreter adapter exists per invocation
same-invocation Python state persists
cross-invocation Python state does not leak
backend dispatch is one typed call
bindings are installed once and sealed
first-party backend result is one structured type
final result is structural, not stdout-parsed
live broker has one owner
root/child Sandbox ownership is unchanged
Volume/persistence scope is unchanged
cleanup failures remain safety-relevant
public API/event/result contracts are unchanged
pinned unit/contract/live gates pass
no new production module/dependency/feature flag was added
```

## 15. Expected benefit

### Before

A maintainer must understand DSPy factory ownership **plus** generation-based binding refresh, async pre-execution reservation, dual backend result shapes, and multiple broker references before changing the interpreter safely.

### After

The mental model becomes:

```text
factory creates invocation
-> configure
-> first execute installs + seals bindings
-> execute typed backend repeatedly
-> DSPy shuts invocation down
-> Fleet separately settles Sandbox lease
```

The primary benefit is lower maintenance and correctness risk. Performance gains are possible from deleting repeated wrapper/rebinding work, but none are claimed until benchmarked.

## 16. Patch status

`fleet-daytona-correctness.patch` implements **PR 1 only**.

Validation performed in the original review environment:

```text
isolated baseline: 5 passed, 3 failed
isolated patched:  8 passed, 0 failed
fragment-level patch application: passed
```

Not yet performed in this environment:

```text
full checkout git apply --check
full Fleet dependency tests
repository Ruff/type checks
live Daytona lifecycle tests
```

See `validation-summary.json` and `IMPLEMENTATION-CHECKLIST.md`.

## 17. Source basis

The exact source facts and URLs used by this PRD are embedded in `BASELINE-AND-EVIDENCE.md`. The architecture choice and alternatives are embedded in `DECISION-RECORD.md`.

This bundle therefore contains the complete decision basis required for implementation review.
