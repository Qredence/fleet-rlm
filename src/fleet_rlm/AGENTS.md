# Backend Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents modifying the backend under `src/fleet_rlm/`.
Read the root [AGENTS.md](../../AGENTS.md) first for shared repo rules.
Consult [src/frontend/AGENTS.md](../frontend/AGENTS.md) only when backend changes affect frontend routes, generated API types, websocket payloads, runtime UX, or shared contract metadata.

## Backend Quickstart

Before editing backend code:

- Read `pyproject.toml` and `Makefile` for canonical commands and package surfaces.
- Inspect the owning package (`api`, `cli`, `runtime`, `integrations`, or `utils`) before adding files.
- Preserve the backend/frontend runtime contract before optimizing internals.
- Keep route/transport modules thin and move business logic into owning runtime or integration modules.
- Avoid import-time side effects in config-only and package-root modules.

Backend source-of-truth files:

- `pyproject.toml` for dependencies and published CLI entrypoints
- `Makefile` for validation and release targets
- `src/fleet_rlm/api/main.py` for app factory, lifespan orchestration, route mounting, and SPA asset resolution
- `src/fleet_rlm/api/bootstrap.py` for runtime bootstrap, optional startup, LM loading, analytics startup, and persistence initialization
- `src/fleet_rlm/cli/fleet_cli.py` and `src/fleet_rlm/cli/main.py` for CLI behavior
- `src/fleet_rlm/runtime/factory.py` for canonical runtime construction
- `src/fleet_rlm/cli/runtime_factory.py` as a compatibility re-export only; new internal code should import `src/fleet_rlm/runtime/factory.py` directly
- `src/fleet_rlm/cli/runners.py` for top-level runner helpers

Artifacts and areas to treat carefully:

- The bundled UI dist output is generated from the frontend build, not handwritten backend source, and may be absent in a fresh source checkout until packaging/build steps run
- `src/fleet_rlm/scaffold/` is curated packaged guidance; update it directly rather than auto-syncing from `.claude/`
- `migrations/` and database-facing schema changes should stay aligned with persistence behavior
- `openapi.yaml` is generated from backend route/schema metadata and should be regenerated, not manually patched

## Agent Priorities

- Preserve the backend/frontend runtime contract before refactoring internals.
- Treat websocket event shape and session lifecycle as shared product surface, not backend-only implementation detail.
- Keep CLI docs and examples aligned with the actual Typer and argparse entrypoints.
- Always run `make format`, `make lint`, and `make typecheck` before commit or PR for backend or shared Python changes.
- Prefer the smallest validation lane that covers the change, then escalate to `make quality-gate` for shared-contract work.
- When route/schema metadata changes, regenerate `openapi.yaml` with `uv run python scripts/openapi_tools.py generate` before frontend sync checks.

## Package Map

Active top-level areas under `src/fleet_rlm/`:

- `agent_host/`: narrow but real Microsoft Agent Framework outer host that wraps the worker seam and hosted policy
- `api/`: thin FastAPI app, auth, routers, schemas, websocket lifecycle, event shaping, and server utilities
- `orchestration_app/`: transitional orchestration bridge that still carries compatibility seams beneath `agent_host/`
- `cli/`: Typer/argparse entrypoints, commands, and runtime builder constructors
- `runtime/`: shared recursive chat/runtime logic, DSPy modules, execution drivers, content processing, tools, and runtime models
- `integrations/`: config, database, observability, MCP, and external-system integrations
- `scaffold/`: packaged Claude Code translation assets exposed by `fleet-rlm init`
- `ui/`: packaged built frontend assets for installed distributions
- `utils/`: shared helpers

## Backend Contract

Published CLI entrypoints from `pyproject.toml`:

- `fleet`
- `fleet-rlm`

Preserve these command surfaces:

- `fleet web`
- `fleet-rlm chat`
- `fleet-rlm serve-api`
- `fleet-rlm serve-mcp`
- `fleet-rlm init`
- `fleet-rlm daytona-smoke`

Important CLI/runtime nuances:

- `fleet web` is a thin entrypoint that delegates into `fleet-rlm serve-api --host 0.0.0.0 --port 8000`
- Daytona websocket requests do not accept request-side `max_depth`; schema enforcement happens server-side
- `/ready` reports critical server readiness only; optional LM and observability warmup status belongs in runtime diagnostics/status

Canonical HTTP and websocket surfaces:

- `/health`
- `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `GET/PATCH /api/v1/runtime/settings`
- `POST /api/v1/runtime/tests/daytona`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`
- `GET /api/v1/runtime/volume/tree`
- `GET /api/v1/runtime/volume/file`
- `GET /api/v1/optimization/status`
- `POST /api/v1/optimization/run`
- `POST /api/v1/traces/feedback`
- `/api/v1/ws/execution`
- `/api/v1/ws/execution/events`
- Optional `/scalar` docs when `scalar_fastapi` is installed

Runtime-mode boundaries:

- The public backend/frontend runtime contract is Daytona-only.
- The canonical chat runtime is the Daytona-backed recursive DSPy ReAct + `dspy.RLM` workbench agent.
- Request-side `runtime_mode` selection is not part of the public websocket contract.
- `execution_mode` remains a per-turn execution hint for the Daytona-backed runtime
- Daytona request controls are `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
- Runtime volume routes are Daytona-only on the public surface

Auth, persistence, and observability constraints:

- Supported auth modes are `dev` and `entra`
- `AUTH_MODE=entra` requires repository-backed tenant admission in addition to token validation
- `PATCH /api/v1/runtime/settings` is blocked unless `APP_ENV=local`
- PostHog and MLflow are live codepaths when configured and should not be treated as no-ops
- In local development, MLflow may auto-start when configured for localhost unless `MLFLOW_AUTO_START=false`

## Agent Operating Rules

Layering rules:

- Keep Agent Framework outer-host orchestration in `agent_host/`; it is real hosted policy, but it should stay narrow and sit around the worker seam
- Keep transport logic in `api/` only
- Treat `orchestration_app/` and `api/orchestration/` as transitional compatibility layers rather than destinations for new core logic
- Keep recursive business/runtime behavior in `worker/`, `runtime/`, or `integrations/daytona/`
- Keep runtime config imports lightweight; config/package-root modules must not import DSPy, provider SDKs, MLflow runtime helpers, or PostHog callbacks as import-time side effects
- Reuse existing helpers before introducing new compatibility wrappers

Runtime ownership:

- Keep DSPy signatures in `runtime/agent/signatures.py`
- Keep runtime model construction/registration in `runtime/models/builders.py`, `runtime/models/registry.py`, or the `fleet_rlm.runtime.models` package exports; do not reference the removed `runtime/models/rlm_runtime_modules.py`
- Keep the main cognition loop in `runtime/agent/chat_agent.py` and `runtime/agent/recursive_runtime.py`
- Keep Daytona execution and durable workspace/session semantics in `integrations/daytona/interpreter.py` and `integrations/daytona/runtime.py`
- Keep runtime orchestration and shared chat/runtime behavior under `runtime/agent/*` and `runtime/execution/*`
- Keep content-oriented helpers under `runtime/content/*`
- Keep DSPy evaluation and optimization helpers under `runtime/quality/*`
- Keep shared evaluation infrastructure in `runtime/quality/datasets.py`, `runtime/quality/scoring_helpers.py`, `runtime/quality/artifacts.py`, `runtime/quality/module_registry.py`, and `runtime/quality/optimization_runner.py`
- Keep per-module optimization entrypoints in `runtime/quality/optimize_*.py`; each must register a `ModuleOptimizationSpec` in the module registry
- The module registry (`module_registry.py`) is the single source of truth for optimizable modules, consumed by CLI, API, and frontend
- GEPA runs offline only — never in the live request path
- Keep grouped tool helpers under:
  - `runtime/tools/content/*`
  - `runtime/tools/sandbox/*`
  - root `runtime/tools/*` only for shared/filesystem/batch/llm entrypoints

API ownership:

- Keep `src/fleet_rlm/api/main.py` limited to app factory, lifespan orchestration, route registration, and SPA mounting
- Keep runtime startup/shutdown in `src/fleet_rlm/api/bootstrap.py`
- Keep `src/fleet_rlm/api/routers/runtime.py` thin; runtime service orchestration belongs in `src/fleet_rlm/api/runtime_services/*`
- Keep websocket runtime preparation in `src/fleet_rlm/api/runtime_services/chat_runtime.py`
- Keep websocket run/session persistence orchestration in `src/fleet_rlm/api/runtime_services/chat_persistence.py`
- Keep websocket event shaping and session lifecycle inside `src/fleet_rlm/api/routers/ws/*`
- Keep `api/orchestration/*` limited to compatibility shims while migration continues

Websocket/runtime contract rules:

- Treat `/api/v1/ws/execution` as the canonical conversational websocket stream
- Treat `/api/v1/ws/execution/events` as the dedicated passive execution/workbench event stream
- Keep `/api/v1/ws/execution` execution-only:
  - accept auth plus websocket frames
  - reject query `session_id`
  - use message-level `session_id` only to allocate, restore, or continue chat sessions
  - emit an early `status` event when initial Daytona startup is slow so the client does not hit first-frame timeout before the real reply or startup error
- Keep `/api/v1/ws/execution/events` subscription-only:
  - require query `session_id`
  - do not route message, cancel, or command frames through this socket
  - emit only `execution_started`, `execution_step`, and `execution_completed`
- Keep websocket workspace/user identity auth-derived on both routes; reject client-supplied `workspace_id` and `user_id`
- Treat `execution_completed.summary` as the canonical workbench/canvas hydration payload
- Keep interpreter-originated REPL execution steps wired through `execution_event_callback` and preserve any previously installed callback when bridging hooks
- Do not reintroduce Daytona-only workbench hydration through chat-final payload scraping
- Daytona-backed chat should emit live canonical `trajectory_step`, `reasoning_step`, `status`, `warning`, `tool_call`, and `tool_result` events during execution
- When Daytona falls back after a controlled failure, preserve the answer but mark the turn as degraded in final payloads and MLflow metadata
- Prefer websocket-first streaming; do not replace workspace/chat streams with SSE without a clear product need

Daytona-specific boundaries:

- Keep Daytona-specific behavior under `integrations/daytona/*`
- Keep async Neon/Postgres persistence under `integrations/database/*` with the concrete `FleetRepository` as the canonical repo boundary
- Keep the lightweight SQLite sidecar for local sessions/history/optimization in `integrations/local_store.py`
- Treat `DaytonaSandboxRuntime` and `DaytonaSandboxSession` as the canonical internal async contract
- Keep Daytona volume browsing in `integrations/daytona/volumes.py`
- Keep Daytona volume readiness/state normalization in `integrations/daytona/runtime_helpers.py`; accept SDK enum-style states such as `VolumeState.READY` in addition to raw tokens like `ready`
- When Daytona volume readiness times out or fails, include both the raw SDK state and the normalized canonical state in diagnostics where they differ
- Keep the durable mounted-volume roots aligned to `/home/daytona/memory/{memory,artifacts,buffers,meta}`
- Treat the live Daytona workspace as transient repo/execution state with no implicit workspace-to-volume sync
- Keep `rlm_query` as the shared agent-level recursive entrypoint; `rlm_query_batched` remains Daytona-only
- Daytona idle lifecycle is timer-driven:
  - `auto_stop_interval=30`
  - `auto_archive_interval=60`
  - treat these values as provider minutes, not seconds

Tooling boundaries:

- The shared backend/frontend product contract is Daytona-only; do not reintroduce Modal provider surfaces.
- Reuse `src/fleet_rlm/utils/regex.py` for regex helpers instead of creating local variants

Common mistakes to avoid:

- Putting business logic into routers or CLI entrypoints
- Adding heavy imports to config/package roots
- Reintroducing parallel Daytona chat/runtime orchestrators outside the shared recursive runtime
- Hand-editing packaged UI build output or generated OpenAPI artifacts
- Treating Volumes or `/ready` semantics differently from the implemented contract

## Canonical Commands

Backend setup and runtime:

- `uv sync --all-extras`
- `uv run fleet web`
- `uv run fleet-rlm serve-api --port 8000`
- `uv run fleet-rlm serve-mcp --transport stdio`
- `uv run python scripts/openapi_tools.py generate`
- `uv run python scripts/openapi_tools.py validate`

Daytona workflow:

- `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]`

## Validation by Change Type

Mandatory baseline for backend or shared Python edits:

- `make format`
- `make lint`
- `make typecheck`

Fast backend confidence:

- `make test-fast`

Focused backend/runtime coverage:

- `uv run pytest -q tests/unit/api/ws/test_messages.py tests/unit/api/ws/test_execution_helpers.py tests/unit/package/test_exports.py tests/ui/server/test_api_contract_routes.py tests/ui/server/test_router_runtime.py tests/ui/ws/test_chat_stream.py`

Daytona-focused backend coverage:

- `uv run pytest -q tests/unit/integrations/daytona/test_config.py tests/unit/integrations/daytona/test_smoke.py tests/unit/integrations/daytona/test_runtime.py tests/unit/integrations/daytona/test_interpreter.py tests/unit/runtime/agent/test_chat_agent_runtime.py`

Shared-contract or release-sensitive work:

- `make quality-gate`

Keep command examples aligned with `Makefile`, `pyproject.toml`, and the live router/schema contract.
