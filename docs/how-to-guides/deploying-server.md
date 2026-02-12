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
  "response": "The value of pi is 3.1415926535",
  "trajectory": [ ... ]
}
```

### `GET /health`

Kubernetes-style health check. Returns `200 OK` if the server and Modal connection are healthy.

### `WS /ws/chat`

WebSocket endpoint for real-time streaming of agent thoughts and tool outputs.

## Configuration

The server is configured via environment variables:

- `DSPY_LM_MODEL`: The LLM to use.
- `RLM_SERVER_TIMEOUT`: Request timeout in seconds.
- `RLM_MAX_STEPS`: Maximum ReAct steps per request.
