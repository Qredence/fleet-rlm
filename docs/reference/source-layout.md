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
- `src/fleet_rlm/interactive/`: interactive data models used by streaming/runtime surfaces

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
