# Source Layout (`src/fleet_rlm`)

This reference describes the package directory layout and what each area is responsible for.

## Core Package

- `src/fleet_rlm/__init__.py`: public exports and package version
- `src/fleet_rlm/cli.py`: Typer CLI entrypoint
- `src/fleet_rlm/runners.py`: high-level orchestrator functions
- `src/fleet_rlm/signatures.py`: DSPy signatures used by RLM workflows

## Runtime Layers

- `src/fleet_rlm/core/`: interpreter runtime, sandbox driver, env configuration
- `src/fleet_rlm/react/`: ReAct agent, tools, command dispatch, streaming events
- `src/fleet_rlm/chunking/`: pure chunking helpers (size/header/timestamp/json)
- `src/fleet_rlm/stateful/`: stateful session and persistence-oriented wrappers
- `src/fleet_rlm/models.py`: streaming data models (`StreamEvent`, `TurnState`) used by `react/streaming.py`
- `src/fleet_rlm/bridge/`: stdio JSON-RPC bridge for Ink TUI

## Optional Service Surfaces

- `src/fleet_rlm/server/`: FastAPI application, routers, schemas, middleware
- `src/fleet_rlm/mcp/`: MCP server runtime and tool surface

## Utilities

- `src/fleet_rlm/utils/`: scaffold install, Modal helpers, reusable utility tools

## Scaffold Assets (Claude Code)

- `src/fleet_rlm/_scaffold/skills/`: packaged skills
- `src/fleet_rlm/_scaffold/agents/`: packaged sub-agent definitions
- `src/fleet_rlm/_scaffold/teams/`: packaged team templates
- `src/fleet_rlm/_scaffold/hooks/`: packaged prompt hooks

These are installed to `~/.claude/` via:

```bash
uv run fleet-rlm init
```

## Current Conventions

- Library/runtime code should live under importable Python modules (`*.py`) in `src/fleet_rlm/`.
- Operational configs and non-package docs should live outside `src/` (for example `config/`, `docs/`).
- Avoid empty placeholder package directories and `__pycache__` directories in source control.

## Frontend (`src/frontend`)

React + TypeScript + Vite single-page application for the Fleet web UI.

- `src/frontend/src/app/App.tsx`: root component (React Router provider)
- `src/frontend/src/app/routes.ts`: route config with lazy-loaded page modules
- `src/frontend/src/app/layout/`: `RootLayout`, `DesktopShell`, `MobileShell`, `RouteSync`
- `src/frontend/src/app/pages/`: page components (`SkillCreationFlow`, `SkillLibrary`, `MemoryPage`, `TaxonomyBrowser`, `AnalyticsDashboard`, `SettingsPage`, auth pages)
- `src/frontend/src/app/components/hooks/`: React hooks (`useChat`, `useSkills`, `useMemory`, `useAuth`, `useFilesystem`, etc.)
- `src/frontend/src/app/components/features/`: feature components (artifacts, settings, command palette, notifications)
- `src/frontend/src/app/components/shared/`: reusable shared components (skeletons, error boundary, toggles)
- `src/frontend/src/app/components/ui/`: Radix UI primitives (shadcn/ui style)
- `src/frontend/src/app/lib/api/`: generic HTTP/SSE API client with snake↔camel case conversion
- `src/frontend/src/app/lib/rlm-api/`: fleet-rlm-specific API layer (WebSocket client, OpenAPI-generated types)
- `src/frontend/src/app/lib/perf/`: lazy route loading with retry (`lazyWithRetry`, `routePreload`)
- `src/frontend/src/app/providers/`: context providers (`AppProviders`, `AuthProvider`, `NavigationProvider`)
