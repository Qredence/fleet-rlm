# Contract Surfaces

Reference for all API contract surfaces in fleet-rlm — OpenAPI spec, WebSocket protocols, streaming events, and sync commands.

---

## OpenAPI Spec

**Location:** `openapi.yaml` at repository root

The OpenAPI spec is the single source of truth for REST endpoints. The frontend TypeScript client is generated from it.

---

## WebSocket Contracts

### Primary Endpoint

```
/api/v1/ws/execution
```

The main WebSocket endpoint for interactive RLM execution. Handles bidirectional communication between frontend and runtime.

### Raw Events Endpoint

```
/api/v1/ws/execution/events
```

Read-only event stream for observability and debugging. Receives all StreamEvents without command dispatch.

---

## WebSocket Message Fields

### Request (client to server)

Canonical Daytona-only fields:

| Field | Type | Description |
|-------|------|-------------|
| `repo_url` | `string` | Repository URL for workspace initialization |
| `repo_ref` | `string` | Git ref (branch, tag, commit) to checkout |
| `context_paths` | `list[string]` | Files/directories to include in context |
| `batch_concurrency` | `int` | Max parallel sandbox operations |

### Common request fields:

| Field | Type | Description |
|-------|------|-------------|
| `message` | `string` | User message text |
| `session_id` | `string` | Session identifier for continuity |
| `execution_mode` | `string` | `"rlm"`, `"direct"`, `"escalating"` |
| `max_llm_calls` | `int` | Budget cap for this turn |
| `tools_enabled` | `list[string]` | Allowed tool names |

---

## StreamEvent Kinds

| Kind | When emitted | Payload |
|------|-------------|---------|
| `"status"` | Lifecycle transitions | `state`, `reason` |
| `"reasoning"` | LLM thinking/CoT | `content`, `token_count` |
| `"tool_call"` | Tool invocation starts | `tool_name`, `tool_input` |
| `"tool_result"` | Tool execution completes | `tool_name`, `result`, `duration_ms` |
| `"text"` | Final response text | `content` |
| `"error"` | Error occurred | `error_type`, `message`, `recoverable` |
| `"done"` | Turn complete | `trajectory`, `history_turns`, `total_tokens` |
| `"clarification"` | Needs user input | `questions`, `context` |

---

## StreamEvent Payload Fields

Common fields present on most events:

| Field | Type | Present on | Description |
|-------|------|-----------|-------------|
| `tool_name` | `string` | `tool_call`, `tool_result` | Name of the tool invoked |
| `tool_input` | `dict` | `tool_call` | Arguments passed to the tool |
| `trajectory` | `list` | `done` | Full execution trace |
| `history_turns` | `int` | `done` | Number of conversation turns |
| `runtime_degraded` | `bool` | `status`, `error` | Whether runtime is in degraded mode |
| `reason` | `string` | `status`, `error` | Human-readable explanation |

---

## API Sync Commands

### Validate (detect drift without changing files)

```bash
make api-check
```

Compares the current `openapi.yaml` against the live FastAPI routes. Exits non-zero if drift is detected.

### Regenerate (update spec and client)

```bash
make api-sync
```

1. Regenerates `openapi.yaml` from FastAPI route definitions
2. Regenerates the frontend TypeScript client

### Frontend Type Generation

```bash
cd src/frontend && pnpm run api:sync && pnpm run api:check
```

- `api:sync` — regenerates TypeScript types from `openapi.yaml`
- `api:check` — validates generated types compile without errors

---

## Contract Drift Indicators

Signs that the API contract is out of sync:

- Frontend shows "undefined" for fields that should have values
- WebSocket messages silently dropped (field name mismatch)
- TypeScript compilation errors in `src/frontend/src/api/`
- `make api-check` exits non-zero in CI
- New route added but not appearing in Swagger UI at `/docs`

---

## Adding New Endpoints

When adding a new route:

1. Implement the FastAPI route with full Pydantic models
2. Run `make api-sync` to regenerate the spec
3. Run `cd src/frontend && pnpm run api:sync` to update TS client
4. Verify with `make api-check` (should exit 0)
5. Add contract test in `tests/unit/api/` covering the new route
