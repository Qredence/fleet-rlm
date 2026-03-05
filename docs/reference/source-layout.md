# Source Layout (`src/fleet_rlm`)

This map reflects the current package layout.

## Top-Level Package

- `src/fleet_rlm/__init__.py`: public exports and version
- `src/fleet_rlm/cli.py`: `fleet-rlm` Typer CLI entrypoint
- `src/fleet_rlm/fleet_cli.py`: `fleet` launcher entrypoint
- `src/fleet_rlm/runners.py`: maintained high-level runner functions
- `src/fleet_rlm/react/signatures.py`: DSPy signatures used in runtime flows

## Core Runtime

- `src/fleet_rlm/core/`: interpreter runtime, sandbox driver, config helpers
- `src/fleet_rlm/react/`: ReAct agent, tool registry, command dispatch, streaming
- `src/fleet_rlm/chunking/`: chunking utilities

## Server Surface

- `src/fleet_rlm/server/main.py`: FastAPI app factory and lifespan
- `src/fleet_rlm/server/config.py`: runtime config and guardrails
- `src/fleet_rlm/server/deps.py`: shared dependencies and server state
- `src/fleet_rlm/server/routers/`: HTTP + WebSocket routers
- `src/fleet_rlm/server/schemas/`: Pydantic schemas
- `src/fleet_rlm/server/auth/`: auth abstraction (`dev` + scaffolded `entra`)

## Data and Persistence

- `src/fleet_rlm/db/`: Neon/Postgres engine, models, repository
- `src/fleet_rlm/models/`: canonical streaming/event models
- `src/fleet_rlm/server/execution/`: execution observability package
- `migrations/`: Alembic migrations for Neon schema

## Optional Service Surface

- `src/fleet_rlm/mcp/`: FastMCP server runtime

## Scaffold Assets

Packaged templates installed by `fleet-rlm init`:

- `src/fleet_rlm/_scaffold/skills/`
- `src/fleet_rlm/_scaffold/agents/`
- `src/fleet_rlm/_scaffold/teams/`
- `src/fleet_rlm/_scaffold/hooks/`

## Frontend Workspace

- `src/frontend/`: React + TypeScript Web UI
- `src/frontend/openapi/fleet-rlm.openapi.yaml`: frontend-side API spec copy

## Notes

The canonical API contract remains `openapi.yaml` at repository root.

Suggested import boundaries:

- FastAPI-only code lives under `src/fleet_rlm/server/`.
- Modal primitives (`modal.*`) should remain in `src/fleet_rlm/core/` (and narrowly-scoped helpers).
- DSPy signatures/modules live under `src/fleet_rlm/react/`; server code can use `dspy.context(...)` but should avoid defining DSPy programs.
