# Implementation checklist

This checklist is the execution companion to `PRD-and-implementation-plan.md`. It is intentionally limited to three PRs and reuses existing test files.

## PR 1 — correctness before simplification

**Status in this bundle:** patch generated; full repository validation still required.

### Files

```text
M src/fleet_rlm/daytona/interpreter.py
M tests/unit/backend/daytona/test_daytona_adapter.py
```

### Code changes

- [x] Reorder `_public_output()` checks so `CodeExecutionError` is tested before `CodeInterpreterError`.
- [x] Change `_run_backend()` compatibility dispatch so call-shape detection happens before execution.
- [x] Ensure backend-origin `TypeError` is propagated and the backend is called once.
- [x] Ensure stdout callback `TypeError` is propagated and the backend is called once.
- [x] Keep legacy backends without `on_stdout` working temporarily.
- [x] Reject an incompatible backend signature before executing anything.
- [x] Add regression cases to the existing Daytona adapter test file only.

### Required checkout verification

```bash
git switch -c fix/daytona-dispatch c3f3d536242c7663cfeecdfbf63670b51630e9aa
git apply --check /path/to/fleet-daytona-correctness.patch
git apply /path/to/fleet-daytona-correctness.patch
uv sync --frozen --dev
uv run pytest tests/unit/backend/daytona/test_daytona_adapter.py
uv run ruff check src/fleet_rlm/daytona/interpreter.py tests/unit/backend/daytona/test_daytona_adapter.py
uv run ruff format --check src/fleet_rlm/daytona/interpreter.py tests/unit/backend/daytona/test_daytona_adapter.py
uv run ty check
git diff --check
```

Then run the repository's established wider backend/contract checks.

## PR 2 — make binding lifetime equal invocation lifetime

**Goal:** remove repeated mutable rebinding while preserving DSPy injection, standalone use, single-flight execution, callbacks, and async bridge behavior.

### Pre-change reference scan

Run at the pinned/current implementation branch:

```bash
rg -n "_BindingTools|_binding_generation|_installed_binding_generation|needs_binding_refresh|_begin_binding_injection|_ensure_binding_mutation_allowed|_reservation_token|_reservation_task|_release_reservation|_acquire_execution|_release_execution|new_invocation" src tests scripts
```

Do not delete a private seam until every production/test owner is classified.

### Intended `interpreter.py` changes

- [ ] Keep `new_invocation()` as the production factory primitive.
- [ ] Keep a nonblocking single-flight execution guard.
- [ ] Allow DSPy to call `interpreter.tools.update(execution_tools)` before the first execute.
- [ ] Allow DSPy to assign `output_fields` before the first execute.
- [ ] Treat observer, budget, request, context capsule, output contract, async bridge and tool-outcome hooks passed by `new_invocation()` as pre-execution configuration.
- [ ] On first execute, build observed host-tool wrappers once and install them in the backend.
- [ ] Install output fields/final-output limit once.
- [ ] Install context binding once.
- [ ] Seal the invocation bindings before remote execution starts.
- [ ] After sealing, reject mutation with `InterpreterReuseError`/a specific existing configuration error rather than silently rebuilding backend bindings.
- [ ] Remove generation counters if no remaining standalone behavior requires them.
- [ ] Remove contextvar/task reservation machinery if DSPy's factory-created invocation no longer needs pre-execution ownership reservation.
- [ ] Preserve `start()` idempotence.
- [ ] Preserve execute single-flight protection.
- [ ] Preserve `shutdown()` idempotence and strict cleanup error propagation.
- [ ] Preserve `invoke_tool()` async bridge behavior and Turn deadline.
- [ ] Preserve output streaming and observer callback behavior.

### Factory/call-site checks

Verify existing factories continue to return a **new invocation adapter**, not the retained template:

```text
src/fleet_rlm/rlm/execution.py
src/fleet_rlm/rlm/recursion.py
src/fleet_rlm/optimization/daytona.py
src/fleet_rlm/optimization/routing.py
scripts/benchmarks/oolong/adapter.py
```

Do not introduce a `DaytonaInterpreterFactory` class unless a concrete call site cannot be expressed as the current zero-argument closure.

### Existing tests to extend, not duplicate

- [ ] `test_daytona_adapter.py`: post-seal mutation rejection and direct standalone use.
- [ ] `test_sandbox_variable_binding.py`: same-invocation state persists; next invocation is fresh.
- [ ] `test_dspy_compat_seam.py`: native DSPy factory injection and shutdown on setup failure.
- [ ] `test_native_dspy_fastapi_vertical_slice.py`: root invocation stays isolated from retained template.
- [ ] `test_recursion_lease_cleanup.py`: child invocation still shuts down independently of child Sandbox ownership.
- [ ] optimization/benchmark tests: each factory still calls `new_invocation()`.

### PR 2 acceptance matrix

| Scenario | Expected |
|---|---|
| DSPy injects tools before first execute | accepted |
| DSPy injects output fields before first execute | accepted |
| first execute begins | bindings seal |
| tool map changes after seal | rejected before backend call |
| output fields change after seal | rejected before backend call |
| two actions in same invocation | namespace persists |
| next invocation on retained root | fresh namespace/broker state |
| two concurrent calls on same adapter | second fails closed; no interleaving |
| failure during SandboxSerializable setup | DSPy finalizes adapter |
| async tool on application bridge | runs on the intended loop/deadline |

## PR 3 — one backend protocol, one result shape, one broker owner

**Goal:** delete compatibility paths after PR 2 establishes stable ownership.

### Backend protocol

Change the internal protocol to exactly:

```python
def run(
    self,
    code: str,
    variables: dict[str, object] | None = None,
    *,
    on_stdout: OutputCallback | None = None,
) -> BackendExecutionResult: ...
```

- [ ] Update `InProcessInterpreterBackend` — already structurally compatible.
- [ ] Update `_SandboxProcessBackend` — already structurally compatible.
- [ ] Update every test double.
- [ ] Delete PR 1's signature-inspection compatibility branch.
- [ ] Make `_run_backend()` one direct call.

### Result unification

- [ ] Change `_finalize()` to accept `BackendExecutionResult` only.
- [ ] Remove raw-string final-payload extraction from the interpreter path when no caller remains.
- [ ] Search before deleting `extract_final_payload`, marker helpers, or legacy submit helpers; keep anything still used by a supported offline/diagnostic path.
- [ ] Keep `FinalOutput` conversion at the DSPy adapter boundary.
- [ ] Preserve repair versus terminal error categorization.
- [ ] Preserve bounded stdout/stderr handling.

### Broker ownership

- [ ] Make `_SandboxProcessBackend` the single live owner of `DaytonaHttpToolBroker`.
- [ ] Remove or reduce adapter-side `_http_broker` ownership if no production path uses it.
- [ ] Keep `broker` as an inspection property only if diagnostics/tests need it; it must not create a second teardown path.
- [ ] Preserve strict broker stop failure propagation from backend close.

### Broker port

- [ ] Thread the adapter's existing non-zero `broker_port` into `_SandboxProcessBackend`, **or** remove the constructor option if repo-wide search proves it unsupported. Preferred: propagate it.
- [ ] Do not add another config field.
- [ ] Keep `broker_port == 0` behavior explicit for host-tool-capable invocations.

### PR 3 tests

Reuse the existing broker and vertical-slice owners to prove:

```text
state persists within invocation
ordinary stdout is not treated as final output
SUBMIT produces structured FinalOutput
sync host tool works
async host tool works
unknown tool fails
stale/duplicate broker result fails
oversized result is bounded/fails as currently contracted
delivery failure is surfaced
shutdown/cleanup failure is surfaced
new invocation gets fresh executable namespace
```

## Release gate

Do not call the program complete until all are true:

- [ ] fast/unit/contract checks are green under pinned dependencies;
- [ ] Ruff and `ty` are green;
- [ ] code-tree/dependency-boundary checks are green if present on the branch;
- [ ] live retained-root test proves two Turns do not share Python globals;
- [ ] approved persistent workspace data remains available across those Turns;
- [ ] disposable child lifecycle still confirms deletion/absence;
- [ ] cancellation does not permit a Sandbox to be reused while owned execution may still be running;
- [ ] strict cleanup failure prevents unsafe reuse;
- [ ] no public API/OpenAPI/event schema changes were introduced unintentionally;
- [ ] no production module, dependency, or permanent feature flag was added.

## Explicit non-goals during these PRs

Do not combine this work with:

```text
Daytona native-context migration
deeper recursion or child fan-out changes
memory redesign
session persistence redesign
snapshot pooling/hot snapshot work
new frontend/TUI behavior
GEPA/optimization algorithm changes
large module reorganization unrelated to interpreter ownership
```
