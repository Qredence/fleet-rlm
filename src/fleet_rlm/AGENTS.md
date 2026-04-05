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
- `src/fleet_rlm/cli/runtime_factory.py` for shared runtime construction
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

- `api/`: FastAPI app, auth, routers, schemas, execution helpers, and server utilities
- `cli/`: Typer/argparse entrypoints, commands, and runtime builder constructors
- `runtime/`: shared chat/runtime logic, DSPy modules, execution drivers, content processing, tools, and runtime models
- `integrations/`: config, database, observability, MCP, and provider-specific integrations
- `scaffold/`: packaged Claude Code translation assets exposed by `fleet-rlm init`
- `ui/`: packaged built frontend assets for installed distributions
- `utils/`: shared helpers

## Backend Contract

Published CLI entrypoints from `pyproject.toml`:

- `fleet`
- `fleet-rlm`
- `rlm-modal`

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
- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`
- `GET /api/v1/runtime/volume/tree`
- `GET /api/v1/runtime/volume/file`
- `GET /api/v1/optimization/status`
- `POST /api/v1/optimization/run`
- `POST /api/v1/traces/feedback`
- `/api/v1/ws/chat`
- `/api/v1/ws/execution`
- Optional `/scalar` docs when `scalar_fastapi` is installed

Runtime-mode boundaries:

- `modal_chat` builds the shared `RLMReActChatAgent`
- `daytona_pilot` uses the same ReAct + `dspy.RLM` backbone with a Daytona-backed interpreter layer
- `execution_mode` is Modal-only
- Daytona request controls are `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
- Runtime volume routes accept an optional `provider=modal|daytona` override for the Volumes page

Auth, persistence, and observability constraints:

- Supported auth modes are `dev` and `entra`
- `AUTH_MODE=entra` requires repository-backed tenant admission in addition to token validation
- `PATCH /api/v1/runtime/settings` is blocked unless `APP_ENV=local`
- PostHog and MLflow are live codepaths when configured and should not be treated as no-ops
- In local development, MLflow may auto-start when configured for localhost unless `MLFLOW_AUTO_START=false`

## Agent Operating Rules

Layering rules:

- Keep transport logic in `api/` only
- Keep business/runtime behavior in `runtime/` or `integrations/`
- Keep runtime config imports lightweight; config/package-root modules must not import DSPy, provider SDKs, MLflow runtime helpers, or PostHog callbacks as import-time side effects
- Reuse existing helpers before introducing new compatibility wrappers

Runtime ownership:

- Keep DSPy signatures in `runtime/agent/signatures.py`
- Keep runtime modules/orchestration under `runtime/agent/*` and `runtime/models/rlm_runtime_modules.py`
- Keep shared chat/runtime behavior under `runtime/agent/*` and `runtime/execution/*`
- Keep content-oriented helpers under `runtime/content/*`
- Keep shared sandbox tools consolidated under `runtime/tools/*`

API ownership:

- Keep `src/fleet_rlm/api/main.py` limited to app factory, lifespan orchestration, route registration, and SPA mounting
- Keep runtime startup/shutdown in `src/fleet_rlm/api/bootstrap.py`
- Keep `src/fleet_rlm/api/routers/runtime.py` thin; runtime service orchestration belongs in `src/fleet_rlm/api/runtime_services/*`
- Keep websocket event shaping and session lifecycle inside `src/fleet_rlm/api/routers/ws/*`

Websocket/runtime contract rules:

- Treat `/api/v1/ws/chat` as the conversational stream and `/api/v1/ws/execution` as the canonical execution/workbench stream
- Do not reintroduce Daytona-only workbench hydration through chat-final payload scraping
- Daytona-backed chat should emit live canonical `trajectory_step`, `reasoning_step`, `status`, `warning`, `tool_call`, and `tool_result` events during execution
- When Daytona falls back after a controlled failure, preserve the answer but mark the turn as degraded in final payloads and MLflow metadata
- Prefer websocket-first streaming; do not replace workspace/chat streams with SSE without a clear product need

Daytona-specific boundaries:

- Keep Daytona-specific behavior under `integrations/providers/daytona/*`, not in a parallel runtime architecture
- Treat `DaytonaSandboxRuntime` and `DaytonaSandboxSession` as the canonical internal async contract
- Keep Daytona volume browsing in `integrations/providers/daytona/volumes.py`
- Keep the durable mounted-volume roots aligned to `/home/daytona/memory/{memory,artifacts,buffers,meta}`
- Treat the live Daytona workspace as transient repo/execution state with no implicit workspace-to-volume sync
- Keep `rlm_query` as the shared agent-level recursive entrypoint; `rlm_query_batched` remains Daytona-only

Modal/tooling boundaries:

- Keep Modal sandbox assets under `runtime/execution/*`
- Keep Modal volume persistence and browsing in `runtime/tools/modal_volumes.py`
- Reuse `src/fleet_rlm/utils/regex.py` for regex helpers instead of creating local variants

Common mistakes to avoid:

- Putting business logic into routers or CLI entrypoints
- Adding heavy imports to config/package roots
- Reintroducing parallel Daytona chat/runtime orchestrators outside the shared ReAct runtime
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

- `uv run pytest -q tests/ui/server/test_api_contract_routes.py tests/ui/server/test_router_runtime.py tests/ui/ws/test_chat_stream.py tests/unit/test_ws_chat_helpers.py`

Daytona-focused backend coverage:

- `uv run pytest -q tests/unit/test_daytona_rlm_config.py tests/unit/test_daytona_rlm_smoke.py tests/unit/test_daytona_runtime.py tests/unit/test_daytona_interpreter.py tests/unit/test_daytona_workbench_chat_agent.py`

Shared-contract or release-sensitive work:

- `make quality-gate`

Keep command examples aligned with `Makefile`, `pyproject.toml`, and the live router/schema contract.
