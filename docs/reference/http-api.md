# HTTP API Reference

This reference documents the REST and WebSocket API surface exposed by `src/fleet_rlm/api/main.py`.

## Overview

| Category | Prefix | Description |
|----------|--------|-------------|
| Health | `/` | Unprefixed health and readiness probes |
| Auth | `/api/v1/auth` | Identity endpoints |
| Runtime | `/api/v1/runtime` | Settings, diagnostics, volume access |
| Sessions | `/api/v1/sessions` | Session state summaries |
| Traces | `/api/v1/traces` | MLflow trace feedback |
| WebSocket | `/api/v1/ws` | Real-time chat and execution streams |

## Authentication

All `/api/v1/*` endpoints require authentication when `AUTH_REQUIRED=true`. Authentication behavior depends on `AUTH_MODE`:

| Mode | Behavior |
|------|----------|
| `dev` | Debug headers, local HS256 tokens, optional identity |
| `entra` | JWKS-backed Entra ID tokens, Neon tenant admission required |

See [Auth Modes](auth.md) for configuration details.

---

## Health Endpoints

Unauthenticated health probes for load balancers and orchestration.

### `GET /health`

Basic liveness check.

**Response:**

```json
{
  "ok": true,
  "version": "0.4.98"
}
```

### `GET /ready`

Readiness check with component status.

**Response:**

```json
{
  "ready": true,
  "planner_configured": true,
  "planner": "ready",
  "database": "ready",
  "database_required": true,
  "sandbox_provider": "modal"
}
```

**Fields:**

| Field | Values | Description |
|-------|--------|-------------|
| `ready` | boolean | Overall readiness |
| `planner` | `ready`, `missing` | Planner LM status |
| `database` | `ready`, `missing`, `disabled`, `degraded` | Database connectivity |

---

## Auth Endpoints

### `GET /api/v1/auth/me`

Returns the authenticated user's identity envelope.

**Response:**

```json
{
  "tenant_claim": "tenant-123",
  "user_claim": "user-456",
  "email": "user@example.com",
  "name": "Jane Doe",
  "tenant_id": "uuid-...",
  "user_id": "uuid-..."
}
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `tenant_claim` | yes | Entra tenant claim identifier |
| `user_claim` | yes | Entra user claim identifier |
| `email` | no | User email from token |
| `name` | no | User display name |
| `tenant_id` | no | Internal tenant ID (after admission in Entra mode) |
| `user_id` | no | Internal user ID (after admission in Entra mode) |

---

## Runtime Endpoints

### `GET /api/v1/runtime/settings`

Returns current runtime settings snapshot.

**Response:**

```json
{
  "env_path": "/path/to/.env",
  "keys": ["DSPY_LM_MODEL", "DSPY_DELEGATE_LM_MODEL", "SECRET_NAME"],
  "values": {
    "DSPY_LM_MODEL": "gpt-4o",
    "DSPY_DELEGATE_LM_MODEL": "gpt-4o-mini"
  },
  "masked_values": {
    "SECRET_NAME": "***"
  }
}
```

### `PATCH /api/v1/runtime/settings`

Updates runtime settings. **Local environment only** (`APP_ENV=local`).

**Request:**

```json
{
  "updates": {
    "DSPY_LM_MODEL": "gpt-4o-mini",
    "DSPY_DELEGATE_LM_MODEL": "gpt-3.5-turbo"
  }
}
```

**Response:**

```json
{
  "updated": ["DSPY_LM_MODEL", "DSPY_DELEGATE_LM_MODEL"],
  "env_path": "/path/to/.env"
}
```

**Allowed keys:** `DSPY_LM_MODEL`, `DSPY_DELEGATE_LM_MODEL`, `DSPY_DELEGATE_LM_SMALL_MODEL`, `SECRET_NAME`, `VOLUME_NAME`

### `GET /api/v1/runtime/status`

Returns runtime status with active models and connectivity test cache.

**Response:**

```json
{
  "app_env": "local",
  "write_enabled": true,
  "ready": true,
  "active_models": {
    "planner": "gpt-4o",
    "delegate": "gpt-4o-mini",
    "delegate_small": ""
  },
  "llm": {
    "model_set": true,
    "api_key_set": true,
    "planner_configured": true
  },
  "modal": {
    "credentials_available": true,
    "secret_name_set": true,
    "secret_name": "LITELLM",
    "configured_volume": "fleet-rlm-volume"
  },
  "tests": {
    "modal": { "ok": true, "latency_ms": 150 },
    "lm": { "ok": true, "latency_ms": 850 }
  },
  "guidance": []
}
```

### `POST /api/v1/runtime/tests/modal`

Tests Modal sandbox connectivity.

**Response:**

```json
{
  "kind": "modal",
  "ok": true,
  "preflight_ok": true,
  "checked_at": "2026-03-09T12:00:00Z",
  "checks": {
    "credentials_available": true,
    "secret_name_set": true
  },
  "guidance": [],
  "latency_ms": 150,
  "output_preview": "ok"
}
```

### `POST /api/v1/runtime/tests/lm`

Tests LLM connectivity.

**Response:**

```json
{
  "kind": "lm",
  "ok": true,
  "preflight_ok": true,
  "checked_at": "2026-03-09T12:00:00Z",
  "checks": {
    "model_set": true,
    "api_key_set": true
  },
  "guidance": [],
  "latency_ms": 850,
  "output_preview": "OK"
}
```

### `GET /api/v1/runtime/volume/tree`

Lists the file tree of the configured Modal Volume.

**Query Parameters:**

| Parameter | Type | Default | Constraints |
|-----------|------|---------|-------------|
| `root_path` | string | `/` | - |
| `max_depth` | integer | `3` | 1-10 |

**Response:**

```json
{
  "volume_name": "fleet-rlm-volume",
  "root_path": "/",
  "nodes": [
    {
      "id": "/",
      "name": "/",
      "path": "/",
      "type": "volume",
      "children": [
        {
          "id": "/docs",
          "name": "docs",
          "path": "/docs",
          "type": "directory",
          "children": []
        }
      ]
    }
  ],
  "total_files": 42,
  "total_dirs": 8,
  "truncated": false
}
```

### `GET /api/v1/runtime/volume/file`

Reads a volume file as UTF-8 text for frontend preview.

**Query Parameters:**

| Parameter | Type | Required | Constraints |
|-----------|------|----------|-------------|
| `path` | string | yes | min length 1 |
| `max_bytes` | integer | no | 1-1,000,000, default 200,000 |

**Response:**

```json
{
  "path": "/README.md",
  "mime": "text/markdown",
  "size": 1234,
  "content": "# Fleet-RLM\n\n...",
  "truncated": false
}
```

---

## Sessions Endpoints

### `GET /api/v1/sessions/state`

Returns lightweight summaries of active in-memory session state.

**Response:**

```json
{
  "ok": true,
  "sessions": [
    {
      "key": "default:anonymous:session-123",
      "workspace_id": "default",
      "user_id": "anonymous",
      "session_id": "session-123",
      "history_turns": 5,
      "document_count": 2,
      "memory_count": 10,
      "log_count": 25,
      "artifact_count": 3,
      "updated_at": "2026-03-09T12:00:00Z"
    }
  ]
}
```

---

## Traces Endpoints

### `POST /api/v1/traces/feedback`

Records human feedback and optional ground truth for an MLflow trace.

**Request:**

```json
{
  "trace_id": "mlflow-trace-uuid",
  "client_request_id": "client-request-123",
  "is_correct": true,
  "comment": "Good response",
  "expected_response": "Alternative expected output"
}
```

**Response:**

```json
{
  "ok": true,
  "trace_id": "mlflow-trace-uuid",
  "client_request_id": "client-request-123",
  "feedback_logged": true,
  "expectation_logged": true
}
```

**Note:** Requires `MLFLOW_ENABLED=true`. Users can only submit feedback for their own traces.

---

## WebSocket Endpoints

Real-time bidirectional communication for chat and execution observability.

---

### `WS /api/v1/ws/chat`

Primary streaming chat interface for RLM conversations. Supports message streaming, cancellation, and command dispatch.

**Connection:**

```text
ws://localhost:8000/api/v1/ws/chat
```

**Authentication:** Bearer token in subprotocol header when `AUTH_REQUIRED=true`.

---

#### Incoming Frame Types

Clients send JSON frames with a `type` field indicating the message kind.

##### `message` — Chat Message

Send a user message to initiate or continue a conversation.

**Payload:**

```json
{
  "type": "message",
  "content": "Explain the architecture of fleet-rlm",
  "docs_path": null,
  "trace": true,
  "trace_mode": "compact",
  "execution_mode": "auto",
  "runtime_mode": "modal_chat",
  "workspace_id": "default",
  "user_id": "anonymous",
  "session_id": "session-uuid"
}
```

**Fields:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | `"message"` | yes | — | Frame type identifier |
| `content` | string | yes | — | User message text |
| `docs_path` | string | no | `null` | Path to preload documents |
| `trace` | boolean | no | `true` | Enable tracing |
| `trace_mode` | `"compact"` \| `"verbose"` \| `"off"` | no | `"compact"` | Trace output verbosity |
| `execution_mode` | `"auto"` \| `"rlm_only"` \| `"tools_only"` | no | `"auto"` | Execution strategy |
| `runtime_mode` | `"modal_chat"` \| `"daytona_pilot"` | no | `"modal_chat"` | Top-level runtime selector |
| `repo_url` | string | no | `null` | Daytona-only repository URL |
| `repo_ref` | string | no | `null` | Daytona branch or commit; requires `repo_url` |
| `context_paths` | string[] | no | `null` | Daytona-only staged local host paths |
| `batch_concurrency` | integer | no | `null` | Daytona-only recursive batch concurrency |
| `workspace_id` | string | no | `"default"` | Workspace identifier |
| `user_id` | string | no | `"anonymous"` | User identifier |
| `session_id` | string | no | auto-generated | Session identifier |

**Execution Modes:**

| Mode | Behavior |
|------|----------|
| `auto` | Full RLM with tools, delegation, and RLM fallback |
| `rlm_only` | Deep reasoning only, no tool execution |
| `tools_only` | Direct tool execution without RLM reasoning |

**Runtime Modes:**

| Mode | Behavior |
|------|----------|
| `modal_chat` | Default product runtime with Modal-backed execution |
| `daytona_pilot` | Experimental Daytona workbench/runtime inside `RLM Workspace` |

When `runtime_mode="daytona_pilot"`:

- `execution_mode` is ignored
- `repo_ref` requires `repo_url`
- request-side `max_depth` is rejected
- Daytona source controls may be included via `repo_url`, `repo_ref`,
  `context_paths`, and `batch_concurrency`

---

##### `cancel` — Cancel In-Flight Request

Request cancellation of the currently streaming turn.

**Payload:**

```json
{
  "type": "cancel"
}
```

**Behavior:** Sets an internal cancel flag. The agent checks this flag during iteration and stops processing, emitting a `cancelled` event.

---

##### `command` — Execute Agent Command

Dispatch a command to the agent for direct execution (outside of chat flow).

**Payload:**

```json
{
  "type": "command",
  "command": "save_buffer",
  "args": {
    "path": "/output/result.txt",
    "content": "Hello, world!"
  },
  "workspace_id": "default",
  "user_id": "anonymous",
  "session_id": "session-uuid"
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"command"` | yes | Frame type identifier |
| `command` | string | yes | Command name to execute |
| `args` | object | yes | Command arguments (must be JSON object) |
| `workspace_id` | string | no | Workspace identifier |
| `user_id` | string | no | User identifier |
| `session_id` | string | no | Session identifier |

**Available Commands:**

| Command | Description |
|---------|-------------|
| `save_buffer` | Save content to volume path |
| `load_volume` | Load file from volume |
| `write_to_file` | Write to sandbox filesystem |
| `resolve_hitl` | Resolve human-in-the-loop prompt |

**Special Command: `resolve_hitl`**

```json
{
  "type": "command",
  "command": "resolve_hitl",
  "args": {
    "message_id": "hitl-msg-uuid",
    "action_label": "Approve"
  }
}
```

---

#### Outgoing Frame Types

The server sends JSON frames in response to client messages and streaming events.

##### `event` — Streaming Event

Emitted during chat turns to stream agent progress.

**Payload:**

```json
{
  "type": "event",
  "data": {
    "kind": "thought",
    "text": "Analyzing the user's request...",
    "payload": {
      "depth": 0,
      "execution_profile": "ROOT_INTERLOCUTOR"
    },
    "timestamp": "2026-03-09T12:00:00.000Z",
    "version": 2,
    "event_id": "event-uuid"
  }
}
```

**Event Kinds:**

| Kind | Description |
|------|-------------|
| `thought` | Agent reasoning step |
| `tool_call` | Tool invocation starting |
| `tool_result` | Tool execution result |
| `delegation` | RLM sub-agent delegation |
| `delegation_result` | Sub-agent result summary |
| `final` | Final response text |
| `cancelled` | Request was cancelled |
| `error` | Error occurred |
| `hitl_request` | Human-in-the-loop prompt |
| `hitl_resolved` | HITL resolution received |

**Event Data Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `kind` | string | Event type identifier |
| `text` | string | Human-readable content |
| `payload` | object \| null | Structured event data |
| `timestamp` | string | ISO 8601 timestamp |
| `version` | integer | Schema version (currently 2) |
| `event_id` | string | Unique event identifier |

**Payload Fields by Kind:**

- **`tool_call`**: `{ tool_name, tool_args, depth, runtime }`
- **`tool_result`**: `{ tool_name, result, depth, runtime }`
- **`delegation`**: `{ query, depth, runtime }`
- **`delegation_result`**: `{ result_preview, depth, runtime }`
- **`final`**: `{ trace_id, run_id, runtime }`
- **`error`**: `{ error_type, code }`

---

##### `command_result` — Command Execution Result

Response to a `command` frame.

**Payload (success):**

```json
{
  "type": "command_result",
  "command": "save_buffer",
  "result": {
    "status": "ok",
    "saved_path": "/output/result.txt"
  },
  "version": 1,
  "event_id": "event-uuid"
}
```

**Payload (error):**

```json
{
  "type": "command_result",
  "command": "save_buffer",
  "result": {
    "status": "error",
    "error": "Path cannot be empty",
    "message_id": null
  },
  "version": 1,
  "event_id": "event-uuid"
}
```

---

##### `error` — Error Frame

Sent when an error occurs that doesn't fit the event stream model.

**Payload:**

```json
{
  "type": "error",
  "code": "planner_missing",
  "message": "Planner LM not configured",
  "details": {
    "error_type": "RuntimeError"
  }
}
```

**Error Codes:**

| Code | Description |
|------|-------------|
| `planner_missing` | No planner LLM configured |
| `llm_timeout` | LLM call timed out |
| `llm_rate_limited` | Rate limit from LLM provider |
| `sandbox_unavailable` | Modal sandbox unreachable |
| `auth_failed` | Authentication failed |
| `auth_provider_missing` | Auth required but no provider |
| `internal_error` | Unhandled exception |

---

### `WS /api/v1/ws/execution`

Dedicated execution observability stream for Artifact Canvas consumers. Provides structured execution graph events separate from the chat stream.

**Connection:**

```text
ws://localhost:8000/api/v1/ws/execution?workspace_id=default&user_id=anonymous&session_id=session-uuid
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `workspace_id` | string | yes | Workspace to subscribe to |
| `user_id` | string | yes | User to subscribe to |
| `session_id` | string | yes | Session to subscribe to |

**Authentication:** Bearer token in subprotocol header when `AUTH_REQUIRED=true`.

---

#### Execution Event Types

All events share a common envelope structure with the event `type` in the top-level.

##### `execution_started` — Run Started

Emitted when a new chat turn begins processing.

**Payload:**

```json
{
  "type": "execution_started",
  "run_id": "default:anonymous:session-uuid:1",
  "workspace_id": "default",
  "user_id": "anonymous",
  "session_id": "session-uuid",
  "step": null
}
```

---

##### `execution_step` — Step Completed

Emitted for each LLM call, tool execution, REPL block, or output.

**Payload:**

```json
{
  "type": "execution_step",
  "run_id": "default:anonymous:session-uuid:1",
  "workspace_id": "default",
  "user_id": "anonymous",
  "session_id": "session-uuid",
  "step": {
    "id": "step-uuid",
    "parent_id": null,
    "type": "llm",
    "label": "Planner reasoning",
    "depth": 0,
    "actor_kind": "root_rlm",
    "actor_id": "agent-uuid",
    "lane_key": "root",
    "input": { "query": "Hello" },
    "output": { "response": "Hi there!" },
    "timestamp": 1709992800.0
  }
}
```

**Step Types:**

| Type | Description |
|------|-------------|
| `llm` | LLM call |
| `tool` | Tool execution |
| `repl` | REPL code block |
| `memory` | Memory operation |
| `output` | Final output |

**Actor Kinds:**

| Kind | Description |
|------|-------------|
| `root_rlm` | Root RLM agent |
| `sub_agent` | Delegated sub-agent |
| `delegate` | Delegate worker |
| `unknown` | Unspecified |

---

##### `execution_completed` — Run Completed

Emitted when a chat turn finishes (success, failure, or cancellation).

**Payload:**

```json
{
  "type": "execution_completed",
  "run_id": "default:anonymous:session-uuid:1",
  "workspace_id": "default",
  "user_id": "anonymous",
  "session_id": "session-uuid",
  "step": {
    "id": "final-step-uuid",
    "parent_id": "step-uuid",
    "type": "output",
    "label": "Final response",
    "depth": 0,
    "actor_kind": "root_rlm",
    "actor_id": "agent-uuid",
    "lane_key": "root",
    "input": null,
    "output": "Here's the answer...",
    "timestamp": 1709992850.0
  }
}
```

---

#### Execution Event Envelope

All execution events share this structure:

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"execution_started"` \| `"execution_step"` \| `"execution_completed"` | Event type |
| `run_id` | string | Unique run identifier |
| `workspace_id` | string | Workspace identifier |
| `user_id` | string | User identifier |
| `session_id` | string | Session identifier |
| `step` | object \| null | Step payload (null for `execution_started`) |

---

## Removed Endpoints

The following endpoints have been removed from the API:

| Endpoint | Status | Notes |
|----------|--------|-------|
| `/api/v1/chat` | Removed | Use `WS /api/v1/ws/chat` instead |
| `/api/v1/auth/login` | Removed | Authentication via bearer tokens only |
| `/api/v1/auth/logout` | Removed | Not applicable for token-based auth |
| `/api/v1/tasks*` | Removed | Task management discontinued |
| `/api/v1/taxonomy*` | Removed | Taxonomy feature discontinued |
| `/api/v1/analytics*` | Removed | Use MLflow traces instead |
| `/api/v1/search` | Removed | Search feature discontinued |
| `/api/v1/memory*` | Removed | Memory feature discontinued |
| `/api/v1/sandbox*` | Removed | Use `/api/v1/runtime/volume/*` for volume access |

---

## Verification

```bash
# Check OpenAPI endpoints
rg -n "^  /" openapi.yaml

# Verify router definitions
rg -n "@router\.(get|post|patch)" src/fleet_rlm/api/routers/

# Check WebSocket routes
rg -n "@router.websocket" src/fleet_rlm/api/routers/ws/api.py
```
