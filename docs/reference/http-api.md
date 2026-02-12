# HTTP API Reference

The `fleet-rlm` server exposes both conversational and task-oriented endpoints via FastAPI.

## Chat Endpoints

### `POST /chat`

Run a single turn of the RLM ReAct loop for a generic question.

**Request:**

```json
{
  "message": "Calculate the factorial of 500",
  "docs_path": "/data/knowledge/math_docs.txt",
  "history": []
}
```

**Response:**

```json
{
  "response": "The text of the agent's answer...",
  "trajectory": [{ "thought": "...", "action": "...", "observation": "..." }]
}
```

### `WS /ws/chat`

WebSocket endpoint for real-time streaming.

**Data Flow:**

- Client sends: `{"message": "..."}`
- Server streams:
  - `{"type": "thought", "data": "Planning..."}`
  - `{"type": "observation", "data": "Result..."}`
  - `{"type": "final", "data": "Answer"}`

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

### `POST /tasks/check-secret`

Verifies that the Modal Secret is accessible.

## Schemas

### `TaskRequest`

Common input schema for task endpoints:

```json
{
  "question": "string",
  "query": "string (optional)",
  "docs_path": "string (optional)",
  "max_iterations": 10,
  "max_llm_calls": 20,
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
