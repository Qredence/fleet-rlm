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
  "history_turns": 1
}
```

### `WS /ws/chat`

WebSocket endpoint for real-time streaming and command dispatch.

**Client -> Server message shapes:**

- `{"type":"message","content":"...","docs_path":null,"trace":true,"trace_mode":"compact"}`
- `{"type":"cancel"}`
- `{"type":"command","command":"...","args":{}}`

**Server -> Client message shapes:**

- Event stream: `{"type":"event","data":{"kind":"...","text":"...","payload":{},"timestamp":"...ISO8601..."}}`
- Command result: `{"type":"command_result","command":"...","result":{}}`
- Error: `{"type":"error","message":"..."}`

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
  "result": {}, // Arbitrary result dict
  "error": "string (if ok=false)"
}
```
