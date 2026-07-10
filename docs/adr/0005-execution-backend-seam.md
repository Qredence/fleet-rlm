# ADR-0005: Execution Backend Seam Behind `stream_turn()`

- **Status:** Accepted (Phase 2A)
- **Date:** 2026-07-07
- **Supersedes:** None
- **Superseded by:** None
- **Related:** ADR-0004 (ChatExecutionContext seam), ADR-0001 (explicit execution modes, deferred), ADR-0002 (RLMAgent, deferred)

## Context

Phase 1 introduced `stream_turn()` as a transport-neutral seam that both the
WebSocket and SSE paths delegate to. Today `stream_turn()` directly calls
`AgentRuntime.aiter_chat_turn_stream()` — there is exactly one execution path
(the legacy DSPy ReAct agent runtime) and no way to select an alternative
backend without editing `stream_turn()` itself.

The Backend Simplification Roadmap calls for eventually collapsing the
`auto`/`rlm_only`/`tools_only` execution modes and introducing a first-class
`RLMAgent` (ADR-0001, ADR-0002, both deferred). Before any of that can land,
we need a stable **dispatch point** behind `stream_turn()` so that future
backends can be added without touching the transport layer again.

## Decision

Introduce an `ExecutionBackend` selector and dispatch through it inside
`stream_turn()`, with **zero behavior change** for the default backend.

### 1. `ExecutionBackend` enum

New module `src/fleet_rlm/api/runtime_services/execution_backend.py`:

```python
from enum import StrEnum

class ExecutionBackend(StrEnum):
    legacy_agent_runtime = "legacy_agent_runtime"
    direct_rlm = "direct_rlm"
```

`StrEnum` (not `Literal`) for stronger typing and `isinstance`-free dispatch
via equality / `match`.

### 2. `AppConfig.execution_backend`

Add a field to `AppConfig` (`src/fleet_rlm/api/config.py`):

```python
execution_backend: ExecutionBackend = Field(
    default=ExecutionBackend.legacy_agent_runtime,
    alias="EXECUTION_BACKEND",
)
```

Env-readable as `EXECUTION_BACKEND`. Default `legacy_agent_runtime` preserves
all Phase 1 behavior.

### 3. `TurnControls.execution_backend`

Add a per-request override field to `TurnControls`
(`src/fleet_rlm/api/runtime_services/chat_context.py`):

```python
execution_backend: ExecutionBackend | None = None
```

Resolution order inside `stream_turn()`:
1. `ctx.controls.execution_backend` if not `None` (per-request override wins)
2. else `AppConfig.execution_backend` (process default)

### 4. `stream_turn()` dispatch

Simple `if/elif` inside `stream_turn()`:

```python
backend = ctx.controls.execution_backend or _resolve_config_backend()

if backend is ExecutionBackend.legacy_agent_runtime:
    # unchanged Phase 1 path: agent_runtime.aiter_chat_turn_stream(**kwargs)
elif backend is ExecutionBackend.direct_rlm:
    raise NotImplementedError(
        "direct_rlm execution backend is not yet implemented"
    )
else:
    raise ValueError(f"Unknown execution backend: {backend!r}")
```

No registry, no strategy ABC, no `BackendRunner` protocol — the `if/elif` is
the smallest change that establishes the seam. A registry/protocol can be
extracted in Phase 2B when the second real backend lands.

The legacy branch receives the context-managed AgentRuntime-like object
explicitly via `stream_turn(*, ctx, agent_runtime, message)`. It must not read
`ctx.prepared.planner_lm` as the runtime; `planner_lm` remains the prepared
DSPy LM dependency. If `agent_runtime` does not expose
`aiter_chat_turn_stream`, the legacy branch raises a clear `TypeError` before
calling `set_execution_mode()` or mutating runtime state.

The legacy branch forwards an explicit allowlist of kwargs supported by
`AgentRuntime.aiter_chat_turn_stream()` (`message`, `cancel_check`, `trace`,
`docs_path`, `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`).
`trace_mode` and `selected_skill_ids` remain transport/context controls and are
not legacy runtime kwargs. Future direct-RLM/runtime implementations may
consume them explicitly.

### 5. `ChatRequest` unchanged

`ChatRequest` (`api/schemas/chat.py`) stays exactly as in Phase 1
(`extra="forbid"`, no `execution_backend` field). The backend is
server-side only: config default + `TurnControls` override set by the
server, never by the client.

### 6. `direct_rlm` evolution (Phase 2A stub → Phase 2D opt-in backend)

At Phase 2A acceptance, `direct_rlm` existed only as the enum value plus a
`raise NotImplementedError(...)` branch — a placeholder for the future
direct-RLM backend (ADR-0002 territory) that failed clearly and early before
any runtime state was mutated.

Subsequent phases implemented the backend behind the same seam:

| Phase | Status |
|-------|--------|
| 2A | Stub — `NotImplementedError` before any agent method |
| 2B | `DirectRLMRunner` skeleton dispatch |
| 2C | Opt-in golden path via `dspy.RLM` + pooled Daytona interpreter |
| 2D | RuntimeEvent parity — `TURN_INPUTS`, trajectory replay, enriched `DONE` |

**Current state (Phase 2D):** `legacy_agent_runtime` remains the default.
`direct_rlm` dispatches to `DirectRLMRunner`, which runs one real RLM turn
through the pooled interpreter and emits `STATUS`, `TURN_INPUTS`, trajectory
replay (`REASONING`/`TOOL_*`), `TEXT`, structured `ERROR`, and enriched
`DONE` (`schema_version`, `history_turns`, `trajectory`,
`execution_backend`). `direct_rlm` is promotion-gated and currently opt-in
(server-side `EXECUTION_BACKEND` only); it is not exposed on `ChatRequest`.

## Consequences

- **Positive:** Establishes the dispatch seam without touching transport,
  schemas, or the OpenAPI contract. Future backends add a branch, not a new
  transport path. Per-request override enables safe admin/test probing
  without an app restart.
- **Positive:** `legacy_agent_runtime` default means every existing Phase 1
  test, the WS regression, and the OpenAPI spec are unchanged.
- **Negative:** `if/elif` will need refactoring into a registry/protocol when
  a third backend appears. Acceptable for Phase 2A; deferred to Phase 2B.
- **Neutral:** `ExecutionBackend` is a new concept distinct from
  `ExecutionMode` (`auto`/`rlm_only`/`tools_only`). The two are orthogonal:
  `ExecutionBackend` selects *which runtime*; `ExecutionMode` selects
  *how the legacy runtime behaves*. They will converge only in Phase 2B+.

## Validation

- Default `AppConfig.execution_backend` is `legacy_agent_runtime`.
- `stream_turn()` with `legacy_agent_runtime` produces byte-identical
  `RuntimeEvent` sequences as Phase 1 (regression test against the existing
  fixture-based stubs).
- `stream_turn()` with `legacy_agent_runtime` rejects a DSPy LM / wrong object
  with a clear `TypeError` before calling `set_execution_mode()`.
- `stream_turn()` with `legacy_agent_runtime` does not forward context-only
  controls such as `trace_mode` to `AgentRuntime.aiter_chat_turn_stream()`.
- `stream_turn()` with `direct_rlm` dispatches to `DirectRLMRunner` (Phase 2B+)
  and emits a `RuntimeEvent` stream including `TURN_INPUTS`, trajectory replay,
  `TEXT`, structured `ERROR`, and enriched `DONE` (Phase 2D). Live streaming
  relay and `MLFLOW_SPAN` parity remain deferred.
- Per-request `TurnControls.execution_backend` override wins over
  `AppConfig.execution_backend`.
- `ChatRequest` schema and OpenAPI artifacts are unchanged
  (`make api-check` clean, no diff in `openapi.yaml`).
- No `live_llm` test required for Phase 2A.
