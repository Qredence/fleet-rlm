# Agent Runtime Execution Flow

This document traces a single chat turn from the WebSocket layer through the cognition module to the final streamed response. It complements [Architecture Overview](../architecture.md) with execution-level detail.

## Primary Entry Point

The [`AgentRuntime`](../../src/fleet_rlm/runtime/agent/runtime.py) class is the primary entry point. It is constructed by [`build_chat_agent()`](../../src/fleet_rlm/runtime/factory.py), the canonical factory used by both the CLI and the FastAPI lifespan.

```python
build_chat_agent(interpreter=..., use_escalation=True)
  → AgentRuntime.__init__()
    → AgentRuntime._build_agent()
      → EscalatingFleetModule (default) or FleetAgent (fallback)
```

`AgentRuntime` composes:

| Component | Source | Purpose |
| --- | --- | --- |
| `agent` | `EscalatingFleetModule` or `FleetAgent` | Cognition module (routing + execution) |
| `interpreter` | `DaytonaInterpreter` | Sandbox lifecycle and code execution |
| `history` | `dspy.History` | Accumulating conversation turns |
| `tools` | `discover_tools()` + bound factories | Registered tool callables |
| `core_memory` | `dict[str, str]` | Persistent key-value memory |

The `_use_escalation` flag (default `True`, toggled by `FLEET_RLM_USE_ESCALATING_RUNTIME`) selects between the three-path `EscalatingFleetModule` and the plain `FleetAgent` (a thin `dspy.ReAct` subclass).

## Three Execution Methods

`AgentRuntime` exposes three turn-execution methods:

| Method | Caller | Behaviour |
| --- | --- | --- |
| `chat_turn(message)` | CLI, tests | Synchronous turn; returns `dspy.Prediction` |
| `achat_turn(message)` | Async callers | Offloads `chat_turn` to a worker thread |
| `aiter_chat_turn_stream(message)` | WebSocket layer | Async generator yielding `RuntimeEvent` objects |

`aiter_chat_turn_stream()` is the canonical streaming entry point. It builds a `TurnContext`, delegates to [`runtime_streaming.aiter_chat_turn_stream()`](../../src/fleet_rlm/runtime/agent/runtime_streaming.py), and yields events consumed by the WebSocket transport.

## Cognition Module: EscalatingFleetModule

[`EscalatingFleetModule`](../../src/fleet_rlm/runtime/modules/escalating.py) is a `dspy.Module` that routes each turn through one of three paths using a typed `dspy.Predict(RouteTurnSignature)` router.

### Turn Preparation

```
forward(user_request, core_memory, history, execution_mode, ...)
  → _prepare_turn()
    → resolve_rlm_routing()    # deterministic checks first
    → _enrich_with_skills()    # SkillSelectionModule selects relevant skills
```

`resolve_rlm_routing()` applies deterministic fast-path rules before the LLM router runs:

- `execution_mode="rlm"` or `"rlm_only"` → go directly to RLM
- URL in the user request → route to the URL-document RLM variant
- Oversized turn context (exceeds `threshold_chars`) → route to the workspace RLM variant
- `force_escalate=True` → bypass the router entirely

### Three Execution Paths

```
                ┌──────────────────────┐
                │  _prepare_turn()     │
                │  resolve_rlm_routing │
                └──────────┬───────────┘
                           │
              deterministic route?
              ┌─────────yes┘
              │              ┌─────────no
              ▼              ▼
         _run_rlm()    _route_turn()
              │          │        │
              │          │   dspy.Predict
              │          │   (RouteTurnSignature)
              │          │        │
              │     ┌────┼────┐   │
              │     │    │    │   │
              │     ▼    ▼    ▼   │
              │  direct tools  rlm│
              │     │    │    │   │
              │     ▼    ▼    ▼   │
              │   CoT  ReAct  RLM │
              └─────┴────┴────┴───┘
```

**Direct path** (`direct`): `dspy.ChainOfThought(RLMReActChatSignature)` — lightweight, no tools, no sandbox. Suitable for simple conversational questions.

**Tools path** (`tools`): `FleetAgent` (a `dspy.ReAct` subclass with `FleetAgentSignature`) — planner predictor generates thoughts and tool selections, extractor generates the final response. Iterates up to `max_iters` with native trajectory truncation.

**RLM path** (`rlm`): `dspy.RLM` running inside a Daytona sandbox. Three RLM variants exist:

| Variant | Signature | When used |
| --- | --- | --- |
| Standard | `RLMTurnSignature` | Default RLM path |
| Workspace | `RLMWorkspaceTurnSignature` | Large context (repo, documents) exceeds threshold |
| URL Document | `RLMDocumentTurnSignature` | User request contains a fetchable URL |

All three variants share the same `dspy.RLM` construction via [`create_runtime_rlm()`](../../src/fleet_rlm/runtime/modules/factory.py) and run in the same Daytona sandbox. The URL-document variant has reduced limits (`max_iterations=4`, `max_llm_calls=8`).

### Degradation and Fallback

Every path degrades gracefully on failure:

```
RLM fails → ChainOfThought fallback with:
  degraded=True
  warning="RLM escalation failed; returned a lightweight fallback response."
  runtime_failure_category="rlm_fallback"

ReAct fails → ChainOfThought fallback with:
  degraded=True
  runtime_failure_category="react_fallback"
```

Degradation markers propagate through the streaming layer and appear in the final `DONE` event payload so the frontend can render warnings.

## Streaming Architecture

[`runtime_streaming.py`](../../src/fleet_rlm/runtime/agent/runtime_streaming.py) provides a single streaming path for all cognition modules. It interleaves three event sources:

### 1. Live Token Streaming

`dspy.streamify` wraps the cognition module with `StreamListener` objects attached to predictors that produce the `response` output field. As the LLM generates tokens, `StreamResponse` chunks are emitted as `RuntimeEvent.TEXT` events with `payload={"streamed": True}`.

Probed predictor paths:

- `respond.predict` (direct path)
- `_react.extract.predict` (tools path via `EscalatingFleetModule`)
- `extract.predict` (bare `FleetAgent`)

### 2. Live Progress Relay

`TurnProgressRelay` carries events emitted from worker threads during RLM/sandbox execution. The streaming loop drains the relay on every iteration and yields events inline:

```python
for event in relay.drain_nonblocking():
    yield event
```

Heartbeats fire every 20 seconds (configurable via `FLEET_RLM_TURN_HEARTBEAT_S`) when no other progress is detected:

```
RuntimeEvent.status("RLM execution in progress (45s)...")
```

### 3. Trajectory Replay

After the final prediction arrives, its trajectory is converted into replay events:

```python
trajectory = _normalize_trajectory(result.trajectory)
for step in trajectory:
    yield RuntimeEvent.reasoning(step.thought)
    yield RuntimeEvent.tool_call(step.tool_name, step.tool_args)
    yield RuntimeEvent.tool_result(step.tool_name, step.observation)
```

Fingerprint deduplication prevents events that already streamed live from being emitted twice:

```python
def _event_fingerprint(event: RuntimeEvent) -> str:
    return f"{kind}:{traj_idx}:{tool_name}:{text_hash}"
```

## Post-Turn Operations

After the prediction is finalized:

1. **History accumulation**: `append_turn_to_history()` adds the user/assistant pair to `dspy.History`, respecting `history_max_turns`.

2. **Summary refresh**: `maybe_refresh_summary()` compacts history when token-budget thresholds are exceeded (70% of 64K context window by default, configurable via `compaction_threshold_pct`).

3. **Observability payload**: `_runtime_observability_payload()` attaches execution metadata to the `DONE` event:

```python
{
    "execution_mode": "auto",
    "runtime_module": "EscalatingFleetModule",
    "escalation_enabled": True,
    "recursion": {"depth": 0, "max_depth": 2},
    "rlm_limits": {"max_iterations": 20, "max_llm_calls": 50, ...},
}
```

4. **Final artifact**: `attach_final_artifact()` extracts any `SUBMIT()` payload from the last tool result and attaches it to the `DONE` event for workbench hydration.

5. **DONE event**: The terminal event carries the full trajectory, history turn count, routing decision, selected skills, degradation markers, and recursion depth state.

## Recursive Depth and History Management (Phase 7)

### History as Native REPL Variable

All RLM turn signatures include `history: dspy.History` as an `InputField`. The RLM module receives the full history object, allowing the model to inspect prior turns with code (e.g., `history.messages[-1]`) rather than relying on flattened recency snippets.

### Bounded Redacted Snapshots

When spawning recursive children, `_build_child_history_snapshot()` extracts the last 2 turns (default), redacts sensitive values (API keys, tokens, passwords), and bounds the snapshot to 2000 characters. Children receive explicit conversation continuity without leaking parent credentials.

### Token-Budget Compaction

`_maybe_refresh_summary()` compacts history based on estimated token usage (4 chars/token approximation). Compaction triggers when:

- History exceeds `compaction_threshold_pct` (default: 70%) of the configured context window
- The turn interval (`summary_interval`, default: 10) is reached

### Explicit Depth Tracking

`_recursion_depth_state()` returns `(depth, max_depth)` from interpreter state. The root runtime is depth 0; `max_depth` defaults to 2. When max depth is reached, `sub_rlm` and `sub_rlm_batched` fall back to `llm_query` and `llm_query_batched`, preventing infinite recursion while preserving answer quality.

## Related Documents

- [Architecture Overview](../architecture.md) — high-level layering and design principles
- [Sandbox Execution Pipeline](../reference/sandbox-execution-pipeline.md) — detailed host↔sandbox interaction during code execution
- [DSPy Daytona Interpreter Boundary](../reference/dspy-daytona-interpreter-boundary.md) — async execution model and RLM budget knobs
- [Daytona Architecture](../reference/daytona-architecture.md) — sandbox lifecycle, volumes, and session continuity
