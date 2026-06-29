# HTTP API Reference

This reference documents the REST and WebSocket API surface exposed by `src/fleet_rlm/api/main.py`.

## Overview

| Category | Prefix | Description |
|----------|--------|-------------|
| Health | `/` | Unprefixed health and readiness probes |
| Auth | `/api/v1/auth` | Identity endpoints |
| Runtime | `/api/v1/runtime` | Settings, diagnostics, volume access |
| Sessions | `/api/v1/sessions` | Session history, turns, stats, export, restore |
| Sandboxes | `/api/v1/sandboxes` | Daytona sandbox management |
| Runs | `/api/v1/runs` | Execution run steps |
| Optimization | `/api/v1/optimization` | GEPA optimization, datasets, runs |
| Traces | `/api/v1/traces` | MLflow trace feedback |
| WebSocket | `/api/v1/ws` | Real-time chat and execution streams |

## Authentication

All `/api/v1/*` endpoints require authentication when `AUTH_REQUIRED=true`. Authentication behavior depends on `AUTH_MODE`:

| Mode | Behavior |
|------|----------|
| `dev` | Debug headers, local HS256 tokens, optional identity |
| `entra` | JWKS-backed Entra ID tokens, Neon tenant admission required |
| `neon` | Neon Auth EdDSA JWTs, Fleet repository admission required |

See [Auth Modes](auth.md) for configuration details.

---

## Health Endpoints

Unauthenticated health probes for load balancers and orchestration.

### `GET /health`

Basic liveness check.

**Response:**

```json
{
  "status": "live",
  "version": "0.6.2"
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
  "sandbox_provider": "daytona"
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
| `tenant_claim` | yes | Auth tenant claim identifier |
| `user_claim` | yes | Auth user claim identifier |
| `email` | no | User email from token |
| `name` | no | User display name |
| `tenant_id` | no | Internal tenant ID (after admission in Entra or Neon mode) |
| `user_id` | no | Internal user ID (after admission in Entra or Neon mode) |

### `POST /api/v1/auth/ws-ticket`

Exchanges a normal authenticated HTTP request for a short-lived, single-use
WebSocket ticket. This is the browser-safe authentication path for Neon Auth
because raw JWTs must not be placed in WebSocket URLs.

**Response:**

```json
{
  "ticket": "opaque-one-time-ticket",
  "expires_at": "2026-06-20T03:30:00Z"
}
```

---

## Runtime Endpoints

### `GET /api/v1/runtime/settings`

Returns current runtime settings snapshot.

**Response:**

```json
{
  "env_path": "/path/to/.env",
  "keys": [
    "DSPY_LM_MODEL",
    "DSPY_DELEGATE_LM_MODEL",
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL"
  ],
  "values": {
    "DSPY_LM_MODEL": "openai/gpt-4o",
    "DSPY_DELEGATE_LM_MODEL": "openai/gpt-4o-mini",
    "DAYTONA_API_KEY": "***",
    "DAYTONA_API_URL": "https://app.daytona.io/api"
  },
  "masked_values": {
    "DSPY_LM_MODEL": "openai/gpt-4o",
    "DSPY_DELEGATE_LM_MODEL": "openai/gpt-4o-mini",
    "DAYTONA_API_KEY": "***",
    "DAYTONA_API_URL": "https://app.daytona.io/api"
  }
}
```

### `PATCH /api/v1/runtime/settings`

Updates runtime settings. Non-Daytona keys are **local environment only**
(`APP_ENV=local`); non-local environments return `403 Forbidden` for those keys.

In hosted `AUTH_MODE=neon` (BYOK routing), `DAYTONA_API_KEY` (and other
`DAYTONA_*` keys) are persisted per-workspace as encrypted ciphertext via
`FLEET_SECRET_ENCRYPTION_KEY` instead of being written to `.env`. A masked
round-trip value (the `sk-…yz` preview returned by `GET /api/v1/runtime/settings`)
is detected and skipped — the stored credential is left untouched and the key
name is reported in `skipped`, not `updated`. Sending an empty value for a key
that already has a stored encrypted value is also a no-op (it does not wipe the
stored credential).

**Request:**

```json
{
  "updates": {
    "DSPY_LM_MODEL": "openai/gpt-4o-mini",
    "DSPY_DELEGATE_LM_MODEL": "openai/gpt-4o-mini"
  }
}
```

**Response:**

```json
{
  "updated": ["DSPY_LM_MODEL", "DSPY_DELEGATE_LM_MODEL"],
  "skipped": [],
  "env_path": "/path/to/.env"
}
```

**Allowed keys:** `DSPY_LM_MODEL`, `DSPY_DELEGATE_LM_MODEL`, `DSPY_DELEGATE_LM_SMALL_MODEL`, `DSPY_DELEGATE_LM_MAX_TOKENS`, `DSPY_LLM_API_KEY`, `DSPY_LM_API_BASE`, `DSPY_LM_MAX_TOKENS`, `DAYTONA_API_KEY`, `DAYTONA_API_URL`, `DAYTONA_TARGET`

### `GET /api/v1/runtime/status`

Returns runtime status with active models and connectivity test cache.

The status payload includes:

- `write_enabled` / `settings_write_enabled` — true only when `.env` runtime
  settings can be patched (`APP_ENV=local`).
- `profile_write_enabled` — true when provider profile writes are allowed
  (`APP_ENV=local` or admitted `AUTH_MODE=neon`).

Hosted Neon profile writes are tenant/user scoped and encrypted; runtime
settings PATCH remains local-only.

**Response:**

```json
{
  "app_env": "local",
  "write_enabled": true,
  "ready": true,
  "sandbox_provider": "daytona",
  "active_models": {
    "planner": "openai/gpt-4o",
    "delegate": "openai/gpt-4o-mini",
    "delegate_small": ""
  },
  "llm": {
    "model_set": true,
    "api_key_set": true,
    "planner_configured": true
  },
  "mlflow": {
    "enabled": false,
    "startup_status": "pending",
    "startup_error": null
  },
  "daytona": {
    "api_key_set": true,
    "api_url_set": true,
    "target_set": true
  },
  "tests": {
    "lm": { "ok": true, "latency_ms": 850 },
    "daytona": { "ok": true, "latency_ms": 640 }
  },
  "guidance": []
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

### `POST /api/v1/runtime/tests/daytona`

Tests Daytona connectivity.

**Response:**

```json
{
  "kind": "daytona",
  "ok": true,
  "preflight_ok": true,
  "checked_at": "2026-03-09T12:00:00Z",
  "checks": {
    "api_key_set": true,
    "api_url_set": true,
    "target_set": true
  },
  "guidance": [],
  "latency_ms": 640,
  "output_preview": "ok"
}
```

### `GET /api/v1/runtime/volume/tree`

Lists the file tree of the configured runtime volume.

**Query Parameters:**

| Parameter | Type | Default | Constraints |
|-----------|------|---------|-------------|
| `root_path` | string | `/` | - |
| `max_depth` | integer | `3` | 1-10 |
| `provider` | `"daytona"` | active backend | - |

**Response:**

```json
{
  "provider": "daytona",
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
| `provider` | `"daytona"` | no | active backend when omitted |

**Response:**

```json
{
  "provider": "daytona",
  "path": "/README.md",
  "mime": "text/markdown",
  "size": 1234,
  "content": "# Fleet-RLM\n\n...",
  "truncated": false
}
```

### `GET /api/v1/runtime/volumes`

Lists all persistent volumes for the active workspace and provider.

**Query Parameters:**

| Parameter | Type | Required | Constraints |
|-----------|------|----------|-------------|
| `provider` | `"daytona"` | no | active backend when omitted |

**Response:**

```json
{
  "provider": "daytona",
  "items": [
    {
      "name": "fleet-rlm-volume",
      "created_at": "2026-03-09T12:00:00Z"
    }
  ]
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

### `GET /api/v1/sessions`

Paginated list of durable session transcripts with search and status filters.

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `search` | string | no | `null` | Full-text search on title |
| `status` | string | no | `null` | Filter by status (`active`, `archived`) |
| `created_after` | datetime | no | `null` | Filter sessions created on or after this date (ISO 8601) |
| `created_before` | datetime | no | `null` | Filter sessions created on or before this date (ISO 8601) |
| `model_name` | string | no | `null` | Filter by exact model name |
| `model_provider` | string | no | `null` | Filter by exact model provider |
| `limit` | integer | no | `20` | Page size (1-100) |
| `offset` | integer | no | `0` | Pagination offset |

**Response:**

```json
{
  "items": [
    {
      "id": "session-uuid",
      "title": "My Chat Session",
      "status": "active",
      "model_name": "openai/gpt-4o",
      "external_session_id": "ext-123",
      "created_at": "2026-03-09T12:00:00Z",
      "updated_at": "2026-03-09T12:05:00Z"
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 20,
  "has_more": false
}
```

### `GET /api/v1/sessions/{session_id}`

Return session metadata and turn count for a specific session.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Identifier of the session to inspect |

**Response:**

```json
{
  "id": "session-uuid",
  "title": "My Chat Session",
  "status": "active",
  "model_name": "openai/gpt-4o",
  "external_session_id": "ext-123",
  "workspace_id": "workspace-uuid",
  "turn_count": 5,
  "created_at": "2026-03-09T12:00:00Z",
  "updated_at": "2026-03-09T12:05:00Z"
}
```

### `PATCH /api/v1/sessions/{session_id}`

Update session title and/or metadata.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Identifier of the session to update |

**Request:**

```json
{
  "title": "New Title",
  "metadata_json": {
    "tags": ["tag1", "tag2"],
    "priority": "high"
  }
}
```

**Response:** Returns `SessionDetailResponse` (same shape as `GET /api/v1/sessions/{session_id}`).

### `DELETE /api/v1/sessions/{session_id}`

Soft-delete (archive) a session.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Identifier of the session to archive |

**Response:**

```json
{
  "ok": true
}
```

### `GET /api/v1/sessions/{session_id}/turns`

Paginated turn-by-turn transcript for a session.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Identifier of the session whose turns to list |

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `limit` | integer | no | `50` | Page size (1-200) |
| `offset` | integer | no | `0` | Pagination offset |

**Response:**

```json
{
  "items": [
    {
      "id": "turn-uuid",
      "turn_index": 1,
      "user_message": "Hello",
      "assistant_message": "Hi there!",
      "created_at": "2026-03-09T12:00:00Z"
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 50,
  "has_more": false
}
```

### `GET /api/v1/sessions/{session_id}/stats`

Aggregated token counts, latency, and model breakdown for all turns in a session.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Identifier of the session whose stats to retrieve |

**Response:**

```json
{
  "total_tokens_in": 1500,
  "total_tokens_out": 800,
  "total_latency_ms": 4500,
  "model_breakdown": {
    "openai/gpt-4o": 3,
    "openai/gpt-4o-mini": 2
  }
}
```

### `POST /api/v1/sessions/{session_id}/restore`

Unarchive (restore) a soft-deleted session. Returns 409 if the session is already active.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Identifier of the session to restore |

**Response:**

```json
{
  "ok": true
}
```

### `POST /api/v1/sessions/{session_id}/export`

Convert a session's turn history into a JSONL dataset suitable for GEPA optimization.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Identifier of the session to export as a dataset |

**Request:**

```json
{
  "module_slug": "my-module"
}
```

**Response:**

```json
{
  "id": "dataset-uuid",
  "name": "My Chat Session (my-module)",
  "row_count": 5,
  "format": "jsonl",
  "module_slug": "my-module",
  "created_at": "2026-03-09T12:00:00Z"
}
```

---

## Sandbox Endpoints

### `GET /api/v1/sandboxes`

List active Daytona sandboxes with id, state, created_at, and volume info.

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | integer | no | `1` | Page number (starting from 1) |
| `limit` | integer | no | `100` | Maximum sandboxes per page (1-1000) |

**Response:**

```json
{
  "items": [
    {
      "id": "sandbox-123",
      "name": "my-sandbox",
      "state": "started",
      "created_at": "2026-03-09T12:00:00Z",
      "volume_name": "fleet-rlm-volume",
      "labels": {},
      "cpu": 2,
      "memory": 4,
      "disk": 10
    }
  ],
  "total": 1,
  "page": 1,
  "total_pages": 1
}
```

### `GET /api/v1/sandboxes/{sandbox_id}`

Return full sandbox details including state, config, and volume.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `sandbox_id` | string | Unique sandbox identifier |

**Response:**

```json
{
  "id": "sandbox-123",
  "name": "my-sandbox",
  "state": "started",
  "created_at": "2026-03-09T12:00:00Z",
  "volume_name": "fleet-rlm-volume",
  "labels": {},
  "cpu": 2,
  "memory": 4,
  "disk": 10,
  "env_vars": {},
  "image": "daytonaio/workspace-resume:latest",
  "snapshot": null,
  "language": null,
  "auto_stop_interval": 30,
  "auto_archive_interval": 60,
  "auto_delete_interval": null,
  "ephemeral": false,
  "network_block_all": false,
  "network_allow_list": null,
  "volumes": []
}
```

### `DELETE /api/v1/sandboxes/{sandbox_id}`

Stop and permanently delete a Daytona sandbox. Returns `204 No Content` on success.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `sandbox_id` | string | Unique sandbox identifier |

### `POST /api/v1/sandboxes/{sandbox_id}/archive`

Archive a Daytona sandbox to cold storage for later recovery.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `sandbox_id` | string | Unique sandbox identifier |

**Response:**

```json
{
  "ok": true
}
```

---

## Runs Endpoints

### `GET /api/v1/runs/{run_id}/steps`

Paginated execution trace steps for a run with step_type, tool_name, tokens, and latency.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `run_id` | string | Identifier of the run whose steps to list |

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `limit` | integer | no | `50` | Page size (1-200) |
| `offset` | integer | no | `0` | Pagination offset |

**Response:**

```json
{
  "items": [
    {
      "id": "step-uuid",
      "step_index": 0,
      "step_type": "tool_call",
      "tool_name": "search",
      "tokens_in": 150,
      "tokens_out": 50,
      "latency_ms": 1200,
      "created_at": "2026-03-09T12:00:00Z"
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 50,
  "has_more": false
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

Real-time websocket communication for conversational turns and execution observability.

---

### `WS /api/v1/ws/execution`

Primary bidirectional streaming interface for RLM conversations. Supports message streaming, cancellation, and command dispatch.

**Connection:**

```text
ws://localhost:8000/api/v1/ws/execution
```

**Authentication:** Browser clients should use `POST /api/v1/auth/ws-ticket` and connect with `ticket=<opaque-ticket>`. Legacy `access_token` query bootstrap is a compatibility path only for modes that explicitly enable it; Neon mode rejects raw JWT query parameters.

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
  "repo_url": "https://github.com/qredence/fleet-rlm.git",
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
| `repo_url` | string | no | `null` | Daytona-only repository URL |
| `repo_ref` | string | no | `null` | Daytona branch or commit; requires `repo_url` |
| `context_paths` | string[] | no | `null` | Daytona-only staged local host paths |
| `batch_concurrency` | integer | no | `null` | Daytona-only recursive batch concurrency |
| `session_id` | string | no | auto-generated | Authoritative client-controlled session selector |

**Execution Modes:**

| Mode | Behavior |
|------|----------|
| `auto` | Full RLM with tools, delegation, and RLM fallback |
| `rlm_only` | Deep reasoning only, no tool execution |
| `tools_only` | Direct tool execution without RLM reasoning |

Daytona-specific request rules:

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
  "session_id": "session-uuid"
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"command"` | yes | Frame type identifier |
| `command` | string | yes | Command name to execute |
| `args` | object | yes | Command arguments (must be JSON object) |
| `session_id` | string | no | Authoritative client-controlled session selector |

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
    "kind": "reasoning_step",
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
| `assistant_token` | Incremental assistant text token |
| `status` | Low-emphasis runtime status update |
| `warning` | Non-fatal warning |
| `reasoning_step` | Agent reasoning step |
| `tool_call` | Tool invocation starting |
| `tool_result` | Tool execution result |
| `trajectory_step` | Trajectory/plan step |
| `plan_update` | Planner update surfaced as a status/tool event |
| `rlm_executing` | Runtime execution milestone |
| `memory_update` | Memory operation update |
| `hitl_request` | Human-in-the-loop prompt |
| `hitl_resolved` | HITL resolution received |
| `command_ack` | Command accepted |
| `command_reject` | Command rejected |
| `final` | Final response text |
| `cancelled` | Request was cancelled |
| `error` | Error occurred |

**Event Data Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `kind` | string | Event type identifier |
| `text` | string | Human-readable content |
| `payload` | object \| null | Event-specific structured data; runtime metadata is carried under `payload.runtime` when available |
| `timestamp` | string | ISO 8601 timestamp |
| `version` | integer | Schema version (currently 2) |
| `event_id` | string | Unique event identifier |

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
| `sandbox_unavailable` | Daytona sandbox unavailable |
| `auth_failed` | Authentication failed |
| `auth_provider_missing` | Auth required but no provider |
| `internal_error` | Unhandled exception |

---

### `WS /api/v1/ws/execution/events`

Dedicated passive execution observability stream for workbench sidepanel consumers. Provides structured execution graph events separate from the chat stream.

**Connection:**

```text
ws://localhost:8000/api/v1/ws/execution/events?session_id=session-uuid
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | yes | Authoritative client-controlled session selector |

**Authentication:** Browser clients should use `POST /api/v1/auth/ws-ticket` and connect with `ticket=<opaque-ticket>`. Legacy `access_token` query bootstrap is a compatibility path only for modes that explicitly enable it; Neon mode rejects raw JWT query parameters.

The backend resolves workspace and user identity from auth claims or server defaults. Client-provided `workspace_id` and `user_id` are rejected; query `session_id` is the authoritative selector for passive execution stream binding. The conversational `/api/v1/ws/execution` route rejects query `session_id`.

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

Step timestamps are numeric Unix epoch seconds produced by the backend; clients may normalize them for display.

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
| `workspace_id` | string | Compatibility field only; canonical workspace identity comes from auth or server defaults |
| `user_id` | string | Compatibility field only; canonical user identity comes from auth or server defaults |
| `session_id` | string | Authoritative client-controlled session selector |
| `step` | object \| null | Step payload (null for `execution_started`) |

---

## Removed Endpoints

The following endpoints have been removed from the API:

| Endpoint | Status | Notes |
|----------|--------|-------|
| `/api/v1/chat` | Removed | Use `WS /api/v1/ws/execution` instead |
| `/api/v1/auth/login` | Removed | Authentication via bearer tokens only |
| Legacy auth logout route | Removed | Not applicable for token-based auth |
| `/api/v1/tasks*` | Removed | Task management discontinued |
| `/api/v1/taxonomy*` | Removed | Taxonomy feature discontinued |
| `/api/v1/skills*` | Removed | Skills feature discontinued |
| `/api/v1/memory*` | Removed | Memory browsing is retired from the canonical API surface |
| `/api/v1/analytics*` | Removed | Use MLflow traces instead |
| `/api/v1/search` | Removed | Search feature discontinued |

---

## Verification

```bash
# Check OpenAPI endpoints
rg -n "^  /" openapi.yaml

# Verify router definitions
rg -n "@router\.(get|post|patch|delete)" src/fleet_rlm/api/routers/

# Check WebSocket routes
rg -n "@router.websocket" src/fleet_rlm/api/routers/ws/endpoint.py
```
