# Fleet-RLM Daytona simplification — implementation plan

**Baseline:** `Qredence/fleet-rlm@c3f3d536242c7663cfeecdfbf63670b51630e9aa`
**Dependencies:** `dspy==3.4.0`, `daytona==0.218.0`
**Goal:** simplify the Daytona `CodeInterpreter` boundary without replacing native DSPy, Sandbox lifecycle, host-tool authorization, or persistence semantics.

This plan is complete without any earlier planning documents.

## 1. Target outcome

After all three PRs:

```text
DSPy factory
  -> fresh DaytonaCodeInterpreter
     -> configure tools/output/context/observer/budget
     -> first execute installs and seals bindings once
     -> repeated execute calls use one typed backend protocol
     -> BackendExecutionResult only
     -> live backend owns one DaytonaHttpToolBroker
     -> DSPy shuts the invocation adapter down

Fleet DaytonaRuntime remains the separate owner of the Sandbox lease.
```

No new production package/module is introduced.

## 2. Normative affected tree

### Production package

```text
src/fleet_rlm/
├── daytona/
│   ├── __init__.py                    # no intended behavior change
│   ├── broker.py                      # PR 3: ownership/result wiring only
│   ├── diagnostics.py                 # change only if private reference requires it
│   ├── errors.py                      # reuse existing errors; no new hierarchy
│   ├── interpreter.py                 # PR 1, PR 2, PR 3 primary implementation
│   ├── runtime.py                     # wiring only if backend constructor changes
│   └── snapshot-requirements.txt      # unchanged
├── rlm/
│   ├── execution.py                   # verify root factory; change only if signature wiring requires it
│   ├── program.py                     # verify native DSPy builder; no algorithm replacement
│   └── recursion.py                   # verify child factory/cleanup; change only if signature wiring requires it
└── optimization/
    ├── daytona.py                     # keep new_invocation factory contract
    └── routing.py                     # keep new_invocation factory contract

scripts/
└── benchmarks/
    └── oolong/
        └── adapter.py                  # keep new_invocation factory contract
```

### Existing tests to extend

```text
tests/
├── unit/
│   ├── backend/
│   │   ├── daytona/
│   │   │   ├── test_daytona_adapter.py
│   │   │   ├── test_sandbox_variable_binding.py
│   │   │   └── test_optimization_evaluator.py
│   │   └── rlm/
│   │       ├── test_dspy_compat_seam.py
│   │       └── test_recursion_lease_cleanup.py
│   ├── optimization/
│   │   └── test_routing_eval.py
│   └── scripts/
│       └── test_run_oolong_predict.py
└── contracts/
    └── backend/
        └── test_native_dspy_fastapi_vertical_slice.py
```

Entries marked “verify/change only if required” are not instructions to edit them unconditionally. Before PR 2/3 deletion work, use `rg` to discover every current reference and update the touch set if the repository has moved since the pinned baseline.

## 3. PR 1 — correctness hardening

### Objective

Prevent accidental backend replay and correct public classification of recoverable execution errors.

### Exact implementation

`src/fleet_rlm/daytona/interpreter.py`:

1. `_public_output()` checks `CodeExecutionError` before `CodeInterpreterError`.
2. `_run_backend()` uses `inspect.signature(...).bind(...)` to choose the temporary legacy call shape **before** executing.
3. No exception from the actual backend call is interpreted as evidence that a different call shape should be retried.

`tests/unit/backend/daytona/test_daytona_adapter.py`:

4. Add the eight regression cases already included in `fleet-daytona-correctness.patch`.

### Definition of done

```text
backend TypeError -> exactly one backend call
stdout callback TypeError -> exactly one backend call
legacy/streaming/variadic call shapes work
incompatible signature fails before call
recoverable public label is Execution error
terminal public label is Execution failed
```

### Patch

The complete PR 1 patch is in `fleet-daytona-correctness.patch`.

## 4. PR 2 — invocation-lifetime binding

### Objective

Make the adapter match DSPy 3.4's factory ownership: configure once, install once, execute many times, shutdown once.

### Step 1 — reference scan

```bash
rg -n "_BindingTools|_binding_generation|_installed_binding_generation|needs_binding_refresh|_begin_binding_injection|_ensure_binding_mutation_allowed|_BINDING_RESERVATION|_reservation_token|_reservation_task|_release_reservation|_acquire_execution|_release_execution|new_invocation" src tests scripts
```

Classify each use as:

```text
production required
standalone/test compatibility
obsolete under invocation factory ownership
```

### Step 2 — retain the factory seam

Keep the public/internal behavior of:

```python
fresh = retained.new_invocation(
    observer=...,
    turn_budget=...,
    turn_request=...,
    async_bridge=...,
    tool_settled=...,
    tool_failed=...,
    context_capsule=...,
    output_contract=...,
)
```

The retained root object must not receive those Run-local values.

### Step 3 — simplify mutable binding state

Replace repeated generation refresh with one small state:

```text
bindings open
bindings sealed
shutdown
```

Before sealing, permit the legitimate setup sequence:

```text
Fleet invocation configuration
DSPy tools.update(...)
DSPy output_fields assignment
SandboxSerializable setup preparation
```

At the first execute boundary:

```text
build observed tool map once
build host wrappers once
bind tools once
bind submit/output metadata once
bind context integrity data once
mark sealed
```

After sealing, mutation is rejected before backend execution.

### Step 4 — remove obsolete reservation machinery

If the reference scan and contract tests confirm it is no longer needed, delete:

```text
binding generation counters
needs_binding_refresh
contextvar pre-execution reservation
reservation token/task state
reservation-specific task callbacks
```

Do **not** remove the direct execute single-flight lock.

### Step 5 — preserve standalone safety

`execute()` remains non-reentrant. Concurrent calls on one invocation adapter fail closed instead of interleaving namespace and callbacks.

### Step 6 — update existing tests

Required contracts:

```text
same invocation: variable survives
new invocation: variable absent
post-seal tools mutation: rejected before backend call
post-seal output field mutation: rejected before backend call
retained template: observer/budget/request untouched
DSPy setup failure: invocation still shut down
async host tool: bridge/deadline preserved
```

## 5. PR 3 — one backend result and one broker owner

### Objective

Delete compatibility branches after PR 2 gives the adapter a stable lifetime.

### Step 1 — tighten `InterpreterBackend`

Final protocol:

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

Update all first-party test doubles in the same PR.

### Step 2 — simplify dispatch

Replace compatibility dispatch with:

```python
return backend.run(code, variables, on_stdout=on_stdout)
```

### Step 3 — typed-only finalization

`_finalize()` accepts `BackendExecutionResult` only.

Delete raw-string final extraction **only after**:

```bash
rg -n "extract_final_payload|FINAL_OUTPUT_MARKER|BackendExecutionResult|_finalize\(" src tests scripts
```

confirms which legacy helpers are still required elsewhere.

### Step 4 — one broker owner

The live backend owns:

```text
broker construction
bridge binding
host tool binding
execution
strict broker stop
```

Adapter `shutdown()` closes the backend. If a `broker` property remains, it is inspection-only and delegates to the backend.

### Step 5 — broker port

Make the existing supported non-zero `broker_port` reach `_SandboxProcessBackend` and `DaytonaHttpToolBroker`, or remove the option only if the repository-wide search shows it has no supported caller. Do not add a second port setting.

### Step 6 — preserve broker safety contracts

Do not weaken:

```text
request-size bound
JSON result validation
tool allowlist
request ID + lease correlation
stale/duplicate result rejection
tool timeout/deadline behavior
delivery failure propagation
strict shutdown failure propagation
```

## 6. Verification sequence

### Each PR

```text
focused tests
ruff check
ruff format --check
ty check
git diff --check
repository dependency/code-tree gates
```

### PR 2/3 native DSPy contracts

Use deterministic LMs and the real pinned DSPy version. Prove factory creation, tool/output injection, setup failure shutdown, and same/new invocation state behavior through DSPy's own RLM path.

### Final live Daytona gate

Run established live scenarios that prove:

```text
root Sandbox can be retained
Python namespace is fresh on next Turn
approved files persist according to existing workspace policy
sync/async host tools work
child Sandbox remains isolated
cancellation does not permit unsafe reuse
broker/cleanup failure is surfaced
child deletion/absence is confirmed
```

## 7. Rollback

Each PR is independently revertible. There is no DB migration, Volume migration, public schema migration, or permanent dual runtime flag in this plan.

## 8. Exit criteria

Stop only when:

```text
native DSPy remains central
bindings install once per invocation
backend action dispatch is exactly once locally
backend result type is singular
broker owner is singular
root/child Sandbox ownership is unchanged
persistence semantics are unchanged
cleanup safety is unchanged
full pinned checks pass
```
