# fleet-rlm

[![PyPI version](https://img.shields.io/pypi/v/fleet-rlm.svg)](https://pypi.org/project/fleet-rlm/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/Qredence/fleet-rlm/actions/workflows/ci.yml/badge.svg)](https://github.com/Qredence/fleet-rlm/actions/workflows/ci.yml)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/fleet-rlm?period=monthly&units=INTERNATIONAL_SYSTEM&left_color=MAGENTA&right_color=BLACK&left_text=downloads%2Fmonth)](https://pepy.tech/projects/fleet-rlm)

![thumbnail](src/frontend/public/branding/thumbnail.png)

`fleet-rlm` is a Web UI-first adaptive recursive language model workspace whose center of gravity is a Daytona-backed recursive DSPy runtime. The repo layers that core behind a thin FastAPI/WebSocket transport and a narrow but real Microsoft Agent Framework outer orchestration host.

[Docs](docs/) | [Contributing](CONTRIBUTING.md) | [Changelog](CHANGELOG.md)

## What This Repo Is Now

The maintained backend is easiest to read in this order:

1. **Recursive DSPy worker/runtime core**
   - `src/fleet_rlm/worker/*`
   - `src/fleet_rlm/runtime/agent/*`
   - `src/fleet_rlm/runtime/models/*`
   - `src/fleet_rlm/integrations/daytona/*`
2. **Agent Framework outer orchestration host**
   - `src/fleet_rlm/agent_host/*`
3. **Thin transport shell**
   - `src/fleet_rlm/api/main.py`
   - `src/fleet_rlm/api/routers/ws/*`
   - `src/fleet_rlm/api/runtime_services/*`
4. **Transitional compatibility layers**
   - `src/fleet_rlm/orchestration_app/*`
   - `src/fleet_rlm/api/orchestration/*`
5. **Offline DSPy quality and optimization layer**
   - `src/fleet_rlm/runtime/quality/*`

That means:

- `runtime/agent/chat_agent.py` and `runtime/agent/recursive_runtime.py` are the main cognition loop.
- `integrations/daytona/interpreter.py` and `integrations/daytona/runtime.py` are the execution and durable-memory substrate.
- `agent_host/workflow.py` is host policy around the worker seam, not the core engine.
- FastAPI/WebSocket modules are transport: auth, request parsing, session extraction, lifecycle, and event-envelope delivery.

The supported app surfaces are `Workbench`, `Volumes`, `Optimization`, and `Settings`. Legacy `taxonomy`, `skills`, `memory`, and `analytics` routes are no longer first-class product surfaces and should fall through to `/404`.

## Why This Repo Exists

- Use a shared recursive DSPy + Daytona workspace for task execution, chat turns, run inspection, and runtime diagnostics.
- Keep the product path goal-first rather than repo-first.
- Preserve one shared frontend and websocket contract instead of parallel runtime modes.
- Ship thin transport and hosting layers around the runtime core rather than duplicating runtime logic in API or orchestration wrappers.
- Expose both a user-facing Web UI and integration surfaces for CLI, HTTP, WebSocket, and MCP workflows.

## Quick Start

Add `fleet-rlm` to a `uv`-managed project and launch the Web UI:

```bash
# Create a project if you do not already have one
uv init

# Add fleet-rlm to the environment
uv add fleet-rlm

# Start the Web UI + API server
uv run fleet web
```

Open `http://127.0.0.1:8000`.

If you already have a `uv` project, skip `uv init` and just run `uv add fleet-rlm`.

Published installs already include built frontend assets, so end users do not need `pnpm`, `vp`, or a separate frontend build step.

## Primary Workflows

### Use the Web UI

```bash
uv run fleet web
```

This starts the main product surface with:

- `Workbench` for adaptive chat and runtime execution
- `Volumes` for runtime-backed file browsing
- `Optimization` for DSPy evaluation and optimization workflows
- `Settings` for runtime configuration and diagnostics

### Use terminal chat

```bash
uv run fleet-rlm chat --trace-mode compact
```

### Run the API directly

```bash
uv run fleet-rlm serve-api --host 127.0.0.1 --port 8000
```

### Enable MCP support

If you want the optional MCP server surface, install the extra first:

```bash
uv add "fleet-rlm[mcp]"
uv run fleet-rlm serve-mcp --transport stdio
```

## Runtime Contract

`fleet-rlm` exposes a Daytona-only runtime contract:

- `execution_mode` remains a per-turn execution hint.
- Requests may include `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`.
- Durable mounted roots remain `memory/`, `artifacts/`, `buffers/`, and `meta/`.

The product is goal-first rather than repo-first. Repositories are one possible source of context, alongside local files, staged documents, pasted content, and URLs.

## CLI Surfaces

This package exposes two command entrypoints:

- `fleet`: lightweight launcher for terminal chat and `fleet web`
- `fleet-rlm`: fuller Typer CLI for API, MCP, scaffold, and Daytona flows

Common commands:

```bash
# Web UI
uv run fleet web

# Terminal chat
uv run fleet
uv run fleet-rlm chat --trace-mode verbose

# FastAPI server
uv run fleet-rlm serve-api --port 8000

# Optional MCP server
uv run fleet-rlm serve-mcp --transport stdio

# Scaffold bundled Claude Code assets
uv run fleet-rlm init --list

# Experimental Daytona validation
uv run fleet-rlm daytona-smoke --repo https://github.com/qredence/fleet-rlm.git --ref main
```

## HTTP and WebSocket Contract

The current frontend/backend contract centers on:

- `/health`
- `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `/api/v1/runtime/*`
- `POST /api/v1/traces/feedback`
- `/api/v1/ws/execution`
- `/api/v1/ws/execution/events`

When `AUTH_MODE=entra`, HTTP and WebSocket access use real Entra bearer-token validation plus Neon-backed tenant admission. Runtime settings writes are intentionally limited to `APP_ENV=local`.

The canonical schema lives in [`openapi.yaml`](openapi.yaml).

## Source Development

From the repo root:

```bash
uv sync --all-extras
uv run fleet web
```

Frontend contributors should use `pnpm` inside `src/frontend`:

```bash
cd src/frontend
pnpm install --frozen-lockfile
pnpm run dev
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build
```

This repo explicitly uses `pnpm` for frontend work even though the packaged frontend is built with Vite+ under the hood.

## Maintenance

Common maintenance commands from the repo root:

```bash
# Clear caches and local generated artifacts
make clean

# Regenerate the canonical FastAPI schema after backend contract or doc-metadata changes
uv run python scripts/openapi_tools.py generate

# Validate the schema quality improvements in-flight
uv run python scripts/openapi_tools.py validate

# Sync frontend OpenAPI artifacts after the root spec changes
cd src/frontend
pnpm run api:sync
```

## Validation

Repo-level validation:

```bash
make test-fast
make quality-gate
make release-artifacts
make release-check

# Focused backend/runtime regression lane
uv run pytest -q tests/ui/server/test_api_contract_routes.py tests/ui/server/test_router_runtime.py tests/ui/ws/test_chat_stream.py tests/unit/integrations/daytona/test_config.py tests/unit/integrations/daytona/test_runtime.py tests/unit/integrations/daytona/test_interpreter.py tests/unit/runtime/agent/test_chat_agent_runtime.py -m "not live_llm and not live_daytona and not benchmark"
```

Focused docs validation:

```bash
uv run python scripts/check_docs_quality.py
uv run python scripts/validate_release.py hygiene
uv run python scripts/validate_release.py metadata
```

## Daytona Notes

Use this order for Daytona work:

1. Set `DAYTONA_API_KEY`, `DAYTONA_API_URL`, and optional `DAYTONA_TARGET`.
2. Run `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch-or-sha>]`.

In local/default-local source checkouts, Daytona config resolution prefers repo `.env` / `.env.local` values over inherited shell exports so branch-local validation uses the checkout's intended credentials.

This repo treats `DAYTONA_API_BASE_URL` as a misconfiguration. Use `DAYTONA_API_URL` instead.

## Documentation Map

- [Documentation index](docs/index.md)
- [Architecture overview](docs/architecture.md)
- [Current architecture and transition note](docs/notes/current-architecture-transition.md)
- [Focused codebase map](docs/reference/codebase-map.md)
- [Python backend module map](docs/reference/module-map.md)
- [Adaptive RLM product spec](docs/explanation/product-spec.md)
- [Installation guide](docs/how-to-guides/installation.md)
- [Developer setup](docs/how-to-guides/developer-setup.md)
- [CLI reference](docs/reference/cli.md)
- [HTTP API reference](docs/reference/http-api.md)
- [Auth reference](docs/reference/auth.md)
- [Frontend/backend integration](docs/reference/frontend-backend-integration.md)
- [Runtime settings](docs/how-to-guides/runtime-settings.md)
- [Using the MCP server](docs/how-to-guides/using-mcp-server.md)
- [MLflow workflows](docs/how-to-guides/mlflow-workflows.md)
