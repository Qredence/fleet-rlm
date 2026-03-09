# HTTP API Reference

This reference documents the REST and WebSocket API surface exposed by `src/fleet_rlm/server/main.py`.

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
  "version": "0.4.95"
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

WebSocket endpoints are documented in detail in the WebSocket API reference. Summary:

### `WS /api/v1/ws/chat`

Primary chat interface for RLM conversations.

**Incoming message types:** `message`, `cancel`, `command`

**Outgoing message types:** `event`, `command_result`, `error`

### `WS /api/v1/ws/execution`

Execution stream for observability consumers.

**Query params:** `workspace_id`, `user_id`, `session_id` (required)

**Event types:** `execution_started`, `execution_step`, `execution_completed`

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
rg -n "@router\.(get|post|patch)" src/fleet_rlm/server/routers/

# Check WebSocket routes
rg -n "@router.websocket" src/fleet_rlm/server/routers/ws/api.py
```
