# Contributing to fleet-rlm

Thanks for contributing.

This guide focuses on current repository workflows.

## Prerequisites

- Python 3.10+
- `uv`
- `bun` (frontend checks)

## Setup

```bash
# from repo root
uv sync --extra dev --extra server --extra mcp
cp .env.example .env
```

## Development Commands

```bash
# from repo root
uv run fleet-rlm --help
uv run fleet --help
```

## Quality Gate (Before PR)

```bash
# from repo root
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"
uv run pytest -q
uv run python scripts/check_release_hygiene.py
uv run python scripts/check_release_metadata.py
uv run python scripts/check_docs_quality.py
```

Optional frontend check (if frontend workspace is in scope):

```bash
# from repo root
cd src/frontend
bun install --frozen-lockfile
bun run check
cd ../..
```

## Documentation Rules

- Update docs in the same PR when behavior changes.
- Keep `docs/index.md` and Diataxis section indexes aligned with active docs.
- Historical docs are preserved under `plans/archive/docs-legacy/` and are not active runbooks.

## Validation Before Merge

At minimum:

```bash
# from repo root
uv run fleet-rlm --help
rg -n "^  /" openapi.yaml
uv run python scripts/check_docs_quality.py
```

If server/API docs changed, also verify WS route references:

```bash
rg -n "@router.websocket" src/fleet_rlm/server/routers/ws/api.py
```
