# HTTP API Reference

The `fleet-rlm` server exposes both conversational and task-oriented endpoints via FastAPI.

## Chat Endpoints

### `POST /chat`

Run a single ReAct chat turn.

**Request:**

```json
{
  "message": "Calculate the factorial of 500",
  "docs_path": "/data/knowledge/math_docs.txt",
  "trace": false
}
```

**Response:**

```json
{
  "assistant_response": "The text of the agent's answer...",
  "trajectory": {},
  "history_turns": 1,
  "guardrail_warnings": []
}
```

Notes:

- `trajectory` is omitted when `trace=false`.
- `guardrail_warnings` is additive and may be an empty array.

### `WS /ws/chat`

WebSocket endpoint for real-time streaming and command dispatch.

**Client -> Server message shapes:**

- Message turn:

```json
{
  "type": "message",
  "content": "...",
  "docs_path": null,
  "trace": true,
  "trace_mode": "compact",
  "workspace_id": "default",
  "user_id": "alice",
  "session_id": "session-123"
}
```

- Cancel:

```json
{ "type": "cancel" }
```

- Command dispatch:

```json
{
  "type": "command",
  "command": "write_to_file",
  "args": { "path": "notes/todo.md", "content": "...", "append": true },
  "workspace_id": "default",
  "user_id": "alice",
  "session_id": "session-123"
}
```

**Server -> Client message shapes:**

- Event stream:

```json
{
  "type": "event",
  "data": {
    "kind": "final",
    "text": "assistant response text",
    "payload": {
      "trajectory": {},
      "final_reasoning": "...",
      "history_turns": 3,
      "guardrail_warnings": []
    },
    "timestamp": "...ISO8601..."
  }
}
```

- Command result: `{"type":"command_result","command":"...","result":{}}`
- Error: `{"type":"error","message":"..."}`

Session identity notes:

- Session cache key: `workspace_id:user_id`.
- If `user_id` is not provided, the server assigns a per-connection anonymous ID.

## Task Endpoints (`/tasks/{type}`)

These endpoints wrap specific `fleet_rlm.runners` functions.

### `POST /tasks/basic`

Runs the `run_basic` runner loop.

### `POST /tasks/architecture`

Runs `run_architecture` (Analysis of docs). Requires `docs_path`.

### `POST /tasks/long-context`

Runs the Long-Context RLM strategy.
**Params:**

- `mode`: "analyze" or "summarize" (via `task_type` in body or inferred)

## Schemas

### `TaskRequest`

Common input schema for task endpoints:

```json
{
  "task_type": "basic",
  "question": "string",
  "query": "string (optional)",
  "docs_path": "string (optional)",
  "max_iterations": 15,
  "max_llm_calls": 30,
  "timeout": 600,
  "chars": 10000,
  "verbose": true
}
```

### `TaskResponse`

```json
{
  "ok": true,
  "result": {},
  "error": "string (if ok=false)"
}
```
