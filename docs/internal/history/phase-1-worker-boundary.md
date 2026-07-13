# Phase 1: worker boundary extraction

## What changed

A new internal worker package now defines a stable boundary for running **one workspace task** from Python without depending on FastAPI or websocket transport types:

- `fleet_rlm.worker.contracts`
  - `WorkspaceTaskRequest`
  - `WorkspaceTaskResult`
  - `WorkspaceEvent`
- `fleet_rlm.worker.streaming`
  - `stream_workspace_task(request)`
- `fleet_rlm.worker.runner`
  - `run_workspace_task(request)`
- `fleet_rlm.worker.adapters`
  - normalization/mapping between runtime `StreamEvent` and `WorkspaceEvent`

## What modules it adapts

The worker boundary is an adapter over the existing chat runtime stream path:

- runtime event model: `fleet_rlm.runtime.models.StreamEvent`
- canonical stream source: `agent.aiter_chat_turn_stream(...)`
- websocket stream loop now calls `stream_workspace_task(...)` and maps worker events back to existing websocket handling.

## What was intentionally not changed

- No frontend protocol changes.
- No FastAPI/websocket route redesign.
- No changes to DSPy recursive core behavior.
- No Daytona internals rewrite.
- No movement of `runtime/content`, `runtime/tools`, `runtime/quality`, or `integrations/daytona` ownership.

## Suggested Phase 2 direction

- Move websocket/HTTP orchestration to call worker request construction directly.
- Keep transport concerns (auth/session/socket) in API routers, but push task execution decisions behind worker calls.
- Expand worker contracts for additional task modes only after preserving existing behavior through parity tests.
