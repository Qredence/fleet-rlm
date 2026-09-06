# ADR 004: Turn interpreter context and child data authority

Status: accepted target; production cutover is gated by native-interpreter
feasibility and benchmark parity. The legacy variant still implements the
[Session runtime ADR](ADR-session-scoped-rlm-state.md).

## Decision

InterpreterContext is fresh per Turn. No correctness may depend on Python globals
surviving a Turn. Durable Session conversation, Workspace files, Memory,
Attachments, and committed Artifacts are the sources for rehydration.
Sandbox capacity may be warm or reused only when a fresh interpreter context and
the correct data authority can be established; capacity reuse does not imply
interpreter-state reuse.

| Resource role | Data authority | Capacity policy |
| --- | --- | --- |
| Root | Authorized Workspace data for the current Turn | Fresh context per Turn |
| SemanticChild | Explicit bounded input only; no Volume | Warm-pool eligible after isolation is proved |
| WorkspaceChild | Explicit restricted Workspace data capability | Mount/access restrictions verified before execution |

BenchmarkSandbox is testing terminology, not a production runtime role or selector.
Settlement remains host-owned; children return evidence and cannot publish a Turn.

```text
Durable Session + authorized Workspace state
  -> Turn admission
  -> fresh InterpreterContext
  -> Root execution
       -> SemanticChild (no Volume)
       -> WorkspaceChild (restricted data)
  -> host settlement
  -> context disposal
```

## Migration gates

Phase 3 proves caller-owned native interpreter execution, context isolation,
tool/output binding, cancellation, and cleanup before Phase 4 defines snapshots
and warm capacity. Phase 5 cuts production over and deletes legacy reuse only
after executable benchmark and public Runtime Event parity gates pass.

This decision supersedes cross-Turn globals as a target contract, but does not
claim that the legacy implementation has already been removed.
