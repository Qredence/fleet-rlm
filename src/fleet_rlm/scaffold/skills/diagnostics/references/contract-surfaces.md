# Contract Surfaces

Reference for all API contract surfaces in fleet-rlm — OpenAPI spec, WebSocket protocols, streaming events, and sync commands.

---

## OpenAPI Spec

**Location:** `openapi.yaml` at repository root

The OpenAPI spec is the single source of truth for REST endpoints. The frontend TypeScript client is generated from it.

---

## WebSocket Contracts

### Primary Endpoint

```text
/api/v1/ws/execution
```

The main WebSocket endpoint for interactive RLM execution. Handles bidirectional communication between frontend and runtime.

### Raw Events Endpoint

```text
/api/v1/ws/execution/events
```

Read-only event stream for observability and debugging. Receives projected execution events without command dispatch.

---

## RuntimeEvent (canonical streaming model)

**Source of truth:** `src/fleet_rlm/runtime/events.py`

The runtime emits `RuntimeEvent` objects end-to-end. The websocket layer projects them via `api/events/project_chat.py` into wire frames with kinds:

- `execution_started`
- `execution_step`
- `execution_completed`

### RuntimeEventKind values

| Kind | When emitted | Notes |
|------|-------------|-------|
| `turn_started` | Session/turn startup | Maps to `execution_started` |
| `status` | Lifecycle transitions | Includes `phase`, optional `runtime` context |
| `reasoning` | LLM thinking/CoT | Structured or replayed from trajectory |
| `tool_call` | Tool invocation starts | `tool` field carries `tool_name`, `tool_args` |
| `tool_result` | Tool execution completes | `tool` field carries output |
| `sandbox_exec` | Sandbox REPL step | Status variant with `phase=sandbox_exec` |
| `rlm_delegate` | Recursive delegate | Status variant with delegate metadata |
| `text` | Response token/chunk | Live `response` stream or final text |
| `warning` | Non-fatal issue | |
| `error` | Terminal failure | Maps to `execution_completed` with `status=failed` |
| `done` | Turn complete | Canonical hydration in `execution_completed.summary` with summarized trajectory/history turns, citations, and final status metadata |
| `clarification` | Needs user input | HITL questions |

---

## Legacy StreamEventLike protocol

Transport helpers accept any object satisfying `StreamEventLike` (`kind`, `text`, `payload`, `timestamp`). Prefer `RuntimeEvent` for all new code paths.

---

## RuntimeEvent payload fields (common)

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

## WebSocket Message Fields

Common fields present on most events:

| Field | Type | Present on | Description |
|-------|------|-----------|-------------|
| `tool_name` | `string` | `tool_call`, `tool_result` | Name of the tool invoked |
| `tool_input` | `string | null` | `tool_call` | Raw tool input passed to the tool |
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
