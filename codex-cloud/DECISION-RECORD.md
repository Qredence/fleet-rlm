# ADR-001: simplify the existing Daytona interpreter boundary in place

**Status:** Accepted for implementation
**Date:** 2026-09-30
**Baseline:** `Qredence/fleet-rlm@c3f3d536242c7663cfeecdfbf63670b51630e9aa`

## Context

Fleet uses native `dspy.RLM` and a custom `DaytonaCodeInterpreter` so model-authored Python runs in Daytona while authorized host tools remain mediated by Fleet. The current implementation is correct in its broad ownership split, but the interpreter adapter contains compatibility and mutable-binding machinery that obscures the simple DSPy 3.4 lifecycle.

The Monty interpreter is useful as a design reference because it treats the DSPy `CodeInterpreter` contract as the primary boundary: configure a factory, create one interpreter per invocation, let DSPy own that object, and keep provider-specific details behind it.

Fleet cannot copy Monty's runtime implementation directly. Daytona differs materially: it is a remote CPython Sandbox with explicit Sandbox/Snapshot/Volume lifecycle, async SDK bridging, retained roots, child isolation, host-tool transport, and application-level cleanup requirements.

## Decision

Use the following ownership model:

```text
DSPy RLM
  owns reasoning loop + per-invocation interpreter finalization

DaytonaCodeInterpreter
  owns DSPy CodeInterpreter adaptation + invocation-local bindings + result/error translation

_SandboxProcessBackend + DaytonaHttpToolBroker
  own the current execution namespace + bounded host-tool transport

DaytonaRuntime
  owns Sandbox leases + roots/children + Volumes + admission + containment + cleanup confirmation

Fleet Turn/application layer
  owns authorization + durable commits + public event/result contracts
```

Simplify that design in three PRs rather than replacing the execution engine.

## Alternatives considered

### A. Split `interpreter.py` and `runtime.py` into many small modules

Rejected as the primary solution. File size is a symptom; splitting without reducing owners, states, and compatibility paths relocates complexity.

### B. Replace the HTTP broker immediately with a bare Daytona `run_code()` / process call

Rejected for this program. The existing broker currently supplies persistent namespace behavior, host-tool callbacks, structured final results, correlation/leases, and explicit containment semantics. Replacing it requires proof of parity and is not a refactor-only change.

### C. Move directly to Daytona native interpreter contexts

Deferred. This can be evaluated later in an isolated spike. Adoption requires evidence for state persistence, cancellation/timeout containment, host callbacks, structured final output, retained-root reuse, and cleanup under the pinned SDK or a deliberately upgraded SDK.

### D. Add a generic interpreter/backend provider registry

Rejected. Fleet has one production runtime requirement here. DSPy's factory is already the required abstraction.

### E. Keep the current implementation unchanged

Rejected because at least two concrete correctness issues are identified, and the repeated-binding/raw-string compatibility paths carry ongoing maintenance cost.

## Invariants

The implementation must preserve all of these:

1. Native `dspy.RLM` remains the cognitive loop.
2. Generated Python never executes in the production host process.
3. A Turn gets a fresh interpreter namespace even when its root Sandbox is retained.
4. State persists across actions inside one interpreter invocation.
5. Registered host tools remain the only host-callable functions reachable from generated code.
6. Async host tools keep using the composition-owned bridge/deadline semantics.
7. `CodeExecutionError` remains recoverable to DSPy; terminal interpreter/protocol failures remain terminal.
8. `SUBMIT` final data is structural and validated; arbitrary stdout cannot become a final result.
9. Interpreter shutdown cannot delete the shared Volume or bypass Sandbox lease ownership.
10. Cleanup/containment failure remains visible and prevents unsafe reuse.
11. No new production dependency or permanent dual-engine feature flag is introduced.

## Consequences

### Positive

- one clear invocation ownership model;
- lower risk of accidental backend re-execution;
- fewer mutable state machines in the adapter;
- one internal backend result shape;
- one broker owner;
- easier DSPy compatibility upgrades because the adapter follows the public CodeInterpreter contract more directly.

### Costs

- PR 2 touches concurrency/binding behavior and therefore requires careful contract tests;
- PR 3 requires migrating old test doubles before deleting string result compatibility;
- the broker remains sizeable after this program because it still has real responsibilities.

## Future reconsideration trigger

A broker replacement may be reconsidered only when an experiment demonstrates all of the following with the intended Daytona SDK version:

```text
persistent Python state across execute calls
safe fresh context per Fleet invocation
host callback transport with bounded/correlated results
structured SUBMIT equivalent
stdout/stderr streaming behavior
absolute deadline and cancellation containment
retained-root reuse without namespace leakage
child isolation
confirmed teardown before lease reuse
no weaker Volume/storage boundary
```

If those are met with materially less code and equal or better operational behavior, that experiment can become a separate PRD.
