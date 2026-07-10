# SSE transport dossier

## Phase 1 — FastAPI SSE transport

- **Order:** `1`
- **Status:** `complete`
- **Track:** `Runtime`
- **Summary:** Add AI SDK UIMessage SSE chat without coupling transport to runtime execution.
- **Commit:** `fcd2a833..987fe06f`

### Goal and stable interfaces

`POST /api/chat` is the AI SDK UIMessage Data Stream Protocol v1 endpoint at
the app root. `ChatExecutionContext` and `stream_turn()` form the
transport-neutral execution seam. The runtime emits `RuntimeEvent`; `project_sse`
maps those events to UIMessage parts while `project_chat` preserves WebSocket
compatibility.

SSE is canonical for transcript streaming because chat is mostly one-way,
deployments and debugging are simpler, AI SDK clients consume the protocol
directly, and the frontend need not understand runtime internals. WebSocket is
retained for terminal, sandbox, and other bidirectional control.

### Non-goals

- Remove the WebSocket execution path during the transport phase.
- Change RLM behavior, Daytona lifecycle, Skills, or tool semantics.
- Add backend-specific branches to the SSE projector.

### Acceptance criteria

- [x] `POST /api/chat` streams AI SDK UIMessage v1 parts.
- [x] The endpoint is `/api/chat`, not `/api/v1/chat`.
- [x] SSE and WebSocket reuse the same runtime and `RuntimeEvent` vocabulary.
- [x] `ChatExecutionContext` is transport neutral.
- [x] SSE projection is separate from runtime execution.
- [x] Invalid request and setup errors fail before the response stream opens.
- [x] Frontend rendering depends on backend-projected event types, not runtime internals.
- [x] WebSocket compatibility remains available.

### Validation

```bash
uv run pytest tests/unit/api/test_chat_sse.py tests/unit/api/test_cross_flows.py
```

### Decisions

- [ADR-0003: API chat AI SDK UIMessage stream](../../../adr/0003-api-chat-ai-sdk-uimessage-stream.md)
- [ADR-0004: Chat execution context seam](../../../adr/0004-chat-execution-context-seam.md)
