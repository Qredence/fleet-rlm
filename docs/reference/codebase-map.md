# fleet-rlm Codebase Map (Wave 7.2)

This document is the canonical architecture and ownership map after Wave 7.2 dead-legacy cleanup.

## System Topology by Package

### `src/fleet_rlm/core/`
- Interpreter and sandbox runtime: `interpreter.py`, `driver.py`, `driver_factories.py`
- Host/sandbox helper layers: `sandbox_tools.py`, `volume_tools.py`, `volume_ops.py`, `session_history.py`, `llm_tools.py`
- Environment + LM config: `config.py`

### `src/fleet_rlm/react/`
- Agent orchestrator: `agent.py`
- Streaming: `streaming.py`, `streaming_citations.py`
- Runtime modules and signatures: `runtime_factory.py`, `rlm_runtime_modules.py`, `signatures.py`
- Tool package (canonical): `tools/`
  - `tools/__init__.py`: tool-list assembly
  - `tools/document.py`, `tools/filesystem.py`, `tools/chunking.py`
  - `tools/sandbox.py`, `tools/sandbox_helpers.py`
  - `tools/delegate.py`, `tools/memory_intelligence.py`
- Delegation + validation + memory/cache concerns: `tool_delegation.py`, `validation.py`, `core_memory.py`, `document_cache.py`

### `src/fleet_rlm/server/`
- App factory/lifespan and route registration: `main.py`
- Runtime state/dependencies/auth/middleware: `deps.py`, `config.py`, `auth/`, `middleware.py`
- Runtime settings utilities: `runtime_settings.py`
- Execution observability package: `execution/`
  - `events.py`, `step_builder.py`, `sanitizer.py`
- Router package root: `routers/`
  - HTTP routers: `auth.py`, `chat.py`, `health.py`, `runtime.py`, `tasks.py`, `sessions.py`, `planned.py`
  - WebSocket package (canonical): `routers/ws/`
    - `api.py`, `helpers.py`, `message_loop.py`, `turn.py`, `streaming.py`, `lifecycle.py`, `session.py`, `session_store.py`, `commands.py`, `repl_hook.py`
- Legacy SQLite support still contract-preserved: `legacy_compat.py`, `legacy_models.py`, `services/`

### `src/fleet_rlm/stateful/`
- Stateful wrappers and managers: `agent.py`, `sandbox.py`
- Shared helpers: `result_adapters.py`, `workspace_ops.py`
- Stateful data models: `models.py`

### `src/fleet_rlm/db/`
- Canonical Neon/Postgres persistence: `engine.py`, `repository.py`, `models.py`, `types.py`

### `src/fleet_rlm/models/`
- Canonical streaming/event models: `streaming.py`
- Public package exports: `__init__.py`

### `src/fleet_rlm/terminal/`
- Terminal chat runtime and command handling: `chat.py`, `commands.py`, `settings.py`, `ui.py`

### `src/fleet_rlm/analytics/`
- PostHog integration + tracing + sanitization: `config.py`, `client.py`, `posthog_callback.py`, `trace_context.py`, `sanitization.py`

## Canonical Ownership Map

- Runtime settings masking/env mutation: `fleet_rlm.server.runtime_settings`
- DSPy signature definitions: `fleet_rlm.react.signatures`
- ReAct tool assembly and tool implementations: `fleet_rlm.react.tools.*`
- WebSocket API surface and turn orchestration: `fleet_rlm.server.routers.ws.*`
- Execution stream models/sanitization/step building: `fleet_rlm.server.execution.*`
- Streaming event/state models: `fleet_rlm.models`
- Legacy SQLite CRUD compatibility models: `fleet_rlm.server.legacy_models`

## Frontend → Backend Contract Map

### Frontend transport sources
- REST client layer: `src/frontend/src/lib/api/*`
- Core backend client layer: `src/frontend/src/lib/rlm-api/*`
- Policy: auth/chat/runtime integrations should use `lib/rlm-api`; legacy `lib/api` auth/chat helpers were removed to avoid stale contract drift.

### Fixed backend contracts used by frontend
- WebSockets:
  - `/api/v1/ws/chat`
  - `/api/v1/ws/execution`
- Runtime endpoints:
  - `GET /api/v1/runtime/settings`
  - `PATCH /api/v1/runtime/settings`
  - `POST /api/v1/runtime/tests/modal`
  - `POST /api/v1/runtime/tests/lm`
  - `GET /api/v1/runtime/status`
- Task/session contracts currently consumed by frontend:
  - `/api/v1/tasks`
  - `/api/v1/sessions`
  - `/api/v1/sessions/state`

### Frontend environment semantics (unchanged)
- `VITE_FLEET_API_URL`
- `VITE_FLEET_WS_URL`
- Derived WS path behavior remains `/api/v1/ws/chat` and `/api/v1/ws/execution`

## WebSocket Flow Map (`server/routers/ws/`)

1. `api.py`
- Entry points: `chat_streaming`, `execution_stream`
- Wires auth, session identity resolution, lifecycle manager, and streaming turn execution

2. `helpers.py`
- Envelope formatting/sanitization/auth helper utilities

3. `message_loop.py`
- Validates incoming WS payloads and resolves/switches session identity

4. `turn.py`
- Turn-level orchestration (`run_id`, lifecycle initialization, command handling)

5. `streaming.py`
- Inner streaming loop and event emission
- Coordinates interpreter hook and step emission behavior

6. `lifecycle.py`
- Execution lifecycle persistence/event emitting wrapper

7. `session.py` + `session_store.py`
- Session manifest/state persistence and synchronization

8. `commands.py` + `repl_hook.py`
- WS command routing and REPL-hook queue management

## Legacy Removal Ledger

| Old Import Path | Canonical Path | Removal Status |
|---|---|---|
| `fleet_rlm.runtime_settings` | `fleet_rlm.server.runtime_settings` | Removed in Wave 7.2 |
| `fleet_rlm.signatures` | `fleet_rlm.react.signatures` | Removed in Wave 7.2 |
| `fleet_rlm.terminal_chat` | `fleet_rlm.terminal.chat` | Removed in Wave 7.2 |
| `fleet_rlm.models.models` | `fleet_rlm.models` / `fleet_rlm.models.streaming` | Removed in Wave 7.2 |
| `fleet_rlm.server.models` | `fleet_rlm.server.legacy_models` | Removed in Wave 7.2 |
| `fleet_rlm.server.execution_events` | `fleet_rlm.server.execution.events` | Removed in Wave 7.2 |
| `fleet_rlm.server.execution_step_builder` | `fleet_rlm.server.execution.step_builder` | Removed in Wave 7.2 |
| `fleet_rlm.server.execution_event_sanitizer` | `fleet_rlm.server.execution.sanitizer` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_helpers` | `fleet_rlm.server.routers.ws.helpers` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_commands` | `fleet_rlm.server.routers.ws.commands` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_lifecycle` | `fleet_rlm.server.routers.ws.lifecycle` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_message_loop` | `fleet_rlm.server.routers.ws.message_loop` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_repl_hook` | `fleet_rlm.server.routers.ws.repl_hook` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_session` | `fleet_rlm.server.routers.ws.session` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_session_store` | `fleet_rlm.server.routers.ws.session_store` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_streaming` | `fleet_rlm.server.routers.ws.streaming` | Removed in Wave 7.2 |
| `fleet_rlm.server.routers.ws_turn` | `fleet_rlm.server.routers.ws.turn` | Removed in Wave 7.2 |
| `fleet_rlm.react.tools_sandbox` | `fleet_rlm.react.tools.sandbox` | Removed in Wave 7.2 |
| `fleet_rlm.react.tools_sandbox_helpers` | `fleet_rlm.react.tools.sandbox_helpers` | Removed in Wave 7.2 |
| `fleet_rlm.react.tools_rlm_delegate` | `fleet_rlm.react.tools.delegate` | Removed in Wave 7.2 |
| `fleet_rlm.react.tools_memory_intelligence` | `fleet_rlm.react.tools.memory_intelligence` | Removed in Wave 7.2 |
| `fleet_rlm.react.filesystem_tools` | `fleet_rlm.react.tools.filesystem` | Removed in Wave 7.2 |
| `fleet_rlm.react.document_tools` | `fleet_rlm.react.tools.document` | Removed in Wave 7.2 |
| `fleet_rlm.react.chunking_tools` | `fleet_rlm.react.tools.chunking` | Removed in Wave 7.2 |

## Last Verified Command Set

```bash
# contract + wiring
uv run pytest -q tests/ui/server/test_api_contract_routes.py tests/ui/ws/test_ws_contract_envelopes.py
cd src/frontend && bun run test:unit src/lib/rlm-api/__tests__/backend-contract.test.ts

# canonical imports + removed legacy path guards
uv run pytest -q tests/unit/test_canonical_imports.py tests/unit/test_removed_legacy_paths.py
rg -n "fleet_rlm\\.(runtime_settings|signatures|terminal_chat|models\\.models|server\\.models|server\\.execution_events|server\\.execution_step_builder|server\\.execution_event_sanitizer|react\\.(tools_sandbox|tools_sandbox_helpers|tools_rlm_delegate|tools_memory_intelligence|filesystem_tools|document_tools|chunking_tools)|server\\.routers\\.(ws_helpers|ws_commands|ws_lifecycle|ws_message_loop|ws_repl_hook|ws_session|ws_session_store|ws_streaming|ws_turn))" src tests

# targeted regressions
uv run pytest -q tests/unit/test_ws_router_imports.py tests/unit/test_ws_chat_helpers.py
uv run pytest -q tests/unit/test_react_tools.py tests/unit/test_react_agent.py tests/unit/test_events.py tests/unit/test_runtime_settings.py

# full quality gate
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"
uv run pytest -q -m "not live_llm and not benchmark"
uv run python scripts/check_release_hygiene.py
uv run python scripts/check_release_metadata.py
cd src/frontend && bun run check
```
