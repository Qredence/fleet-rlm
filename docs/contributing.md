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
- Keep `docs/index.md` active section aligned with maintained docs.
- Treat `docs/artifacts`, `docs/plans`, `docs/references`, and `docs/explanation` as historical/research archives unless explicitly modernizing them.

## Validation Before Merge

At minimum:

```bash
# from repo root
uv run fleet-rlm --help
rg -n "^  /" openapi.yaml
```

If you changed server/API docs, also verify WS route references:

```bash
rg -n "@router.websocket" src/fleet_rlm/server/routers/ws.py
```
