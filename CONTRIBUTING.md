# Contributing to fleet-rlm

Fleet RLM is a Python 3.11–3.13 backend with a standalone TypeScript terminal
client. Keep changes focused, preserve the backend architecture invariants, and
update documentation in the same change when behavior moves.

## Setup

```bash
# from repo root
uv sync --all-extras --dev
zsh .codex/workspace-bootstrap.zsh
uv run fleet-rlm --help
```

Node 22+ and pnpm are required only for `tools/fleet-tui/`:

```bash
pnpm --dir tools/fleet-tui install --frozen-lockfile
```

Use local `.env` files or the deployment secret manager for credentials.
Runtime configuration uses the canonical `FLEET_*` environment surface.
Never commit credentials or use a Daytona API key as a Fleet bearer token.

## Architecture rules

- Read `AGENTS.md`, `src/fleet_rlm/AGENTS.md`, `CONTEXT-MAP.md`, and the
  relevant context before changing backend ownership.
- Keep FastAPI routes as HTTP translators and retrieve lifespan-composed
  modules through `api/dependencies.py`.
- Keep Daytona SDK imports inside `src/fleet_rlm/daytona/`.
- Keep Runtime Events transport-neutral; `api/sse.py` owns public projection.
- Let Alembic own live schema evolution. Do not add startup `create_all`.
- Do not add `/api/v1`, WebSocket execution, legacy-backend aliases, or a
  generated frontend contract.
- Do not hand-edit `openapi.yaml`; use `make api-sync`.

## Development workflow

Create a focused branch, add tests at the module interface, and run the smallest
relevant lane while iterating:

```bash
uv run pytest tests/unit/backend/path_to_test.py -q
uv run ruff check path/to/changed.py
uv run ty check src/fleet_rlm
```

For terminal-client changes:

```bash
pnpm --dir tools/fleet-tui test
pnpm --dir tools/fleet-tui typecheck
```

Before requesting review, run:

```bash
make check
make check-docs
make check-release
git diff --check
```

Run `make api-check` whenever HTTP contracts may have moved. Live Daytona/DSPy
tests require explicit `FLEET_LIVE=1` and provider credentials; document any
live lane that was intentionally not run.

## Documentation and generated files

- `docs/index.md` is the documentation reachability root.
- `docs/architecture.md` and `docs/reference/codebase-map.md` describe the
  maintained architecture.
- `scripts/README.md` inventories supported top-level helper scripts.
- Superseded plans and removed-backend documentation belong in Git history, not
  a parallel active docs tree.
- Regenerate `openapi.yaml` with `make api-sync` and verify it with
  `make api-check`.

## Submitting changes

Use clear conventional commits such as `feat:`, `fix:`, `refactor:`,
`test:`, `docs:`, or `chore:`. Pull requests should explain behavior
before and after, list validation commands and results, and call out any
credentialed or live verification that remains outstanding.

Report security vulnerabilities through the process in `SECURITY.md` rather
than a public issue.
