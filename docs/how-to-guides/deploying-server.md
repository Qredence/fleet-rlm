# Deploying the API Server

`fleet-rlm` includes a production-ready **FastAPI** server that exposes RLM capabilities via HTTP and WebSockets. This guide explains how to deploy and interact with it.

## Running the Server

Use the CLI to start the server:

```bash
uv run fleet-rlm serve-api --port 8000 --host 0.0.0.0
```

Once running, you can access the interactive API usage documentation (Scalar) at:
`http://localhost:8000/scalar`

## Request Lifecycle

The server handles requests by spinning up an isolated RLM agent for each conversation turn.

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI (/chat)
    participant Runner as Task Runner
    participant Agent as RLM Agent
    participant Sandbox as Modal Cloud

    Client->>API: POST /chat {"message": "analyze data.csv"}
    activate API
    API->>Runner: spawn_task(chat_request)
    activate Runner
    Runner->>Agent: Initialize Agent & History
    activate Agent

    rect rgb(240, 240, 240)
        note right of Agent: ReAct Loop
        Agent->>Sandbox: execute_code()
        Sandbox-->>Agent: result
    end

    Agent-->>Runner: Final Trajectory & Response
    deactivate Agent
    Runner-->>API: JSON Response
    deactivate Runner
    API-->>Client: 200 OK
    deactivate API
```

## Key Endpoints

### `POST /chat`

Stateless chat endpoint. Checks for RLM completion in a single turn.

**Request:**

```json
{
  "message": "Calculate pi to 10 digits",
  "session_id": "optional-session-id"
}
```

**Response:**

```json
{
  "assistant_response": "The value of pi is 3.1415926535",
  "trajectory": {},
  "history_turns": 1,
  "guardrail_warnings": []
}
```

### `GET /health`

Kubernetes-style health check. Returns `200 OK` if the server and Modal connection are healthy.

### `WS /ws/chat`

WebSocket endpoint for real-time streaming of agent thoughts and tool outputs.

## Configuration

The server is configured by Hydra config plus environment-backed model credentials.

Common runtime knobs:

- `interpreter.timeout`
- `interpreter.async_execute`
- `agent.guardrail_mode` (`off`, `warn`, `strict`)
- `agent.min_substantive_chars`
- `rlm_settings.max_iters`
- `rlm_settings.max_llm_calls`
- `rlm_settings.max_depth`

Example:

```bash
uv run fleet-rlm serve-api --host 0.0.0.0 --port 8000 \
  interpreter.async_execute=true \
  agent.guardrail_mode=warn \
  rlm_settings.max_iters=8
```

For planner LM setup, export `DSPY_LM_MODEL` and (`DSPY_LLM_API_KEY` or `DSPY_LM_API_KEY`).
