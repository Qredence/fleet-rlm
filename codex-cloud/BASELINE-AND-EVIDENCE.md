# Fleet-RLM Daytona simplification: baseline and evidence record

**Evidence date:** 2026-09-30
**Repository:** `Qredence/fleet-rlm`
**Pinned review commit:** `c3f3d536242c7663cfeecdfbf63670b51630e9aa`
**Purpose:** make the PRD and patch understandable without any external planning documents.

This file records the facts used by the implementation plan. It distinguishes source-observed behavior from proposed changes. It is not an additional implementation layer.

## 1. Certified baseline

At the pinned commit, `pyproject.toml` declares:

```text
fleet-rlm 0.7.10
Python >=3.11,<3.14
dspy==3.4.0
daytona==0.218.0
```

The reviewed source blobs are:

```text
src/fleet_rlm/daytona/interpreter.py
  f09b26b7320be9cb850f509dc25c199837b2d2c1

tests/unit/backend/daytona/test_daytona_adapter.py
  aa5861cac8367d02505e2484ccb061299536162e
```

The implementation program intentionally does **not** change those dependency pins.

## 2. Architectural facts verified from source

### 2.1 Native DSPy owns the interpreter lifecycle

DSPy 3.4's code-interpreter contract is factory based. `dspy.RLM` creates an interpreter for an invocation, injects execution tools and output metadata, and shuts that interpreter down in a `finally` path.

DSPy distinguishes:

```text
CodeExecutionError   -> recoverable submitted-code failure
CodeInterpreterError -> terminal interpreter / host setup / protocol failure
```

`CodeExecutionError` is a subclass of `CodeInterpreterError`.

**Consequence for Fleet:** the Daytona adapter should be invocation-scoped and should not rebuild a second RLM lifecycle around DSPy.

### 2.2 Fleet already has an invocation factory seam

`DaytonaCodeInterpreter.new_invocation(...)` creates a fresh interpreter adapter while reusing the underlying retained Sandbox when appropriate. The source explicitly states that DSPy owns shutdown of the fresh adapter while Fleet owns the retained root Sandbox lease.

Invocation-local inputs include:

```text
observer
observation_max_chars
turn_budget
turn_request
async_bridge
tool_settled
tool_failed
context_capsule
output_contract
```

The retained template is therefore already the correct place to source a zero-argument DSPy factory from; a second interpreter provider framework is unnecessary.

### 2.3 The live backend is stateful and broker-backed

`_SandboxProcessBackend` stores invocation state including:

```text
output fields
final-output character limit
bound host tools
context binding
async bridge
tool settlement callbacks
run scratch path
DaytonaHttpToolBroker reference
```

On first execution it creates `DaytonaHttpToolBroker`, binds the async bridge and registered tools, then executes setup plus user code through that broker.

The backend returns a structured `BackendExecutionResult` containing:

```text
stdout
stderr
final
error
error_category
context_accesses
```

**Consequence:** removing the broker is not a small cleanup. It would replace the current execution engine and host-tool transport and therefore belongs in a separate experiment, not this simplification program.

### 2.4 The broker owns the executable namespace

The sandbox-local broker process maintains the Python namespace between execution requests. It also:

- executes generated Python inside the Daytona Sandbox;
- captures bounded stdout/stderr;
- recognizes the structured Fleet final-output exception;
- exposes correlated host-tool requests;
- accepts exactly one correlated result per leased request;
- rejects stale/duplicate/unknown result deliveries;
- executes only tools registered by Fleet on the host side.

No generated Python is intended to execute in the production Fleet host process.

### 2.5 Root Sandbox ownership is separate from invocation ownership

`DaytonaRuntime` manages reusable session-scoped root Sandboxes and disposable child environments. A fresh invocation adapter may be shut down without deleting the retained root Sandbox or its shared persistence scope.

**Required invariant:** DSPy may own the invocation interpreter object; Fleet still owns Sandbox acquisition, retention, child deletion, Volume scoping, admission, and cleanup confirmation.

## 3. Concrete correctness findings

### Finding F-01: `_run_backend()` can replay an action after `TypeError`

At the pinned commit the implementation is effectively:

```python
try:
    return run(code, variables, on_stdout=on_stdout)
except TypeError:
    return run(code, variables)
```

The `TypeError` handler surrounds **execution**, not signature selection. A supported backend can raise `TypeError` after side effects have begun, or `on_stdout` can raise `TypeError`. In either case the method performs a second backend call.

That violates an at-most-once local-dispatch property.

The supplied patch changes the sequence to:

```text
inspect callable signature
-> bind supported call shape
-> invoke exactly once
```

The compatibility branch is intentionally temporary. PR 3 standardizes the backend protocol so the branch can be removed.

### Finding F-02: `_public_output()` checks an exception superclass first

At the pinned commit:

```python
if isinstance(result, CodeInterpreterError):
    return "Execution failed"
if isinstance(result, CodeExecutionError):
    return "Execution error"
```

Because `CodeExecutionError` subclasses `CodeInterpreterError`, the second branch cannot be reached.

This is a helper-level public classification defect. The main `_execute_once()` exception handler already distinguishes recoverable `CodeExecutionError` from terminal interpreter errors.

### Finding F-03: invocation binding state is more complicated than the current ownership model requires

The adapter currently maintains:

```text
_binding_generation
_installed_binding_generation
_BindingTools mutation hooks
contextvar binding reservation
reservation token
reservation task
execution-started flag
reservation state lock
execution lock
```

`_ensure_bindings()` then rebuilds observed host-tool wrappers and refreshes backend bindings when generation state changes.

This machinery was reviewed as the principal maintainability target because production already uses fresh invocation adapters. The target design keeps single-flight execution protection but removes repeated rebinding semantics from a single invocation.

This is a **proposed refactor**, not a claim that the current code is broken.

### Finding F-04: backend result handling still supports a legacy raw-string path

`_finalize()` accepts either `BackendExecutionResult` or raw `str`. For a raw string it attempts final-payload extraction from text.

Both reviewed first-party backends can return `BackendExecutionResult`. The target design makes that shape mandatory internally and deletes string-based final detection once all existing test doubles/callers are migrated.

This is a simplification opportunity, not a defect by itself.

### Finding F-05: broker port configuration is split

The adapter stores `broker_port`, and brokerless host-tool dispatch is explicitly rejected when that value is zero. The live backend construction reviewed at the pinned commit creates `DaytonaHttpToolBroker` using `DEFAULT_BROKER_PORT`.

PR 3 should either propagate the supported non-zero adapter port into the live backend or remove unsupported configurability. The recommendation is to make the existing constructor argument effective without adding a new setting.

## 4. Facts versus proposals

| Item | Status |
|---|---|
| DSPy 3.4 factory lifecycle | Verified source fact |
| Fleet `new_invocation()` ownership statement | Verified source fact |
| Broker executes Python and mediates host tools | Verified source fact |
| Root Sandboxes may be retained; child environments are disposable | Verified source fact |
| `_run_backend()` catches execution-time `TypeError` and retries | Verified source fact |
| `_public_output()` superclass order is wrong | Verified source fact |
| Binding reservations/generations should be removed | Proposed simplification |
| Internal backend return should be `BackendExecutionResult` only | Proposed simplification |
| Broker should remain for this program | Normative design decision based on verified current responsibilities |
| Daytona native interpreter contexts should replace broker later | Not decided; deferred experiment only |

## 5. Full intended Daytona package tree

The plan adds no production module to the Daytona package:

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

Known integration owners that must be reference-scanned before PR 2/3 are:

```text
src/fleet_rlm/rlm/execution.py
src/fleet_rlm/rlm/recursion.py
src/fleet_rlm/rlm/program.py
src/fleet_rlm/optimization/daytona.py
src/fleet_rlm/optimization/routing.py
scripts/benchmarks/oolong/adapter.py
```

Known directly relevant existing tests include:

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

The real checkout must be searched before deleting any private symbol; this list is the reviewed integration inventory, not a substitute for `rg`.

## 6. Source register

The following sources define the evidence baseline:

1. Fleet dependency contract:
   `https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/pyproject.toml`
2. Fleet Daytona interpreter:
   `https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/src/fleet_rlm/daytona/interpreter.py`
3. Fleet Daytona broker:
   `https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/src/fleet_rlm/daytona/broker.py`
4. Fleet Daytona runtime:
   `https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/src/fleet_rlm/daytona/runtime.py`
5. Fleet adapter tests:
   `https://github.com/Qredence/fleet-rlm/blob/c3f3d536242c7663cfeecdfbf63670b51630e9aa/tests/unit/backend/daytona/test_daytona_adapter.py`
6. DSPy 3.4 interpreter protocol:
   `https://github.com/stanfordnlp/dspy/blob/3.4.0/dspy/primitives/code_interpreter.py`
7. DSPy 3.4 RLM implementation:
   `https://github.com/stanfordnlp/dspy/blob/3.4.0/dspy/predict/rlm.py`
8. Monty DSPy interpreter reference:
   `https://github.com/dbreunig/dspy-monty-interpreter/blob/main/README.md`
9. Daytona interpreter documentation:
   `https://www.daytona.io/docs/en/python-sdk/async/async-code-interpreter/`
10. Daytona persistence/snapshots:
   `https://www.daytona.io/docs/en/persistence/`
11. Daytona Volumes:
   `https://www.daytona.io/docs/volumes/`

The PRD is fully specified from the facts above. No prior architecture-plan document is needed to interpret or implement it.
