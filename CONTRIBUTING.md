# Contributing to fleet-rlm

Fleet RLM is a Python 3.11–3.13 backend with a standalone TypeScript terminal
client. Keep changes focused, preserve architecture invariants, and update
documentation in the same change when behavior moves.

## Setup

Ordinary local development:

```bash
uv sync --all-extras --dev
uv run fleet-rlm --help
```

Codex Cloud workspaces use `zsh .codex/workspace-bootstrap.zsh`; do not run that
branch-guarded bootstrap as a generic local setup step.

Node 22.19+ and pnpm are required for `tools/fleet-tui/`:

```bash
pnpm --dir tools/fleet-tui install --frozen-lockfile
```

Use local `.env` files or an authorized secret manager for credentials. Runtime
configuration uses only `FLEET_*`; see
[`docs/reference/configuration.md`](docs/reference/configuration.md). Never
commit credentials or use a Daytona API key as an API bearer token.

## Architecture rules

- Read `AGENTS.md`, `src/fleet_rlm/AGENTS.md`, `CONTEXT-MAP.md`, and the relevant
  context before changing backend ownership.
- Keep routes as HTTP translators and retrieve runtime modules through
  `api/dependencies.py`.
- Keep Daytona SDK imports inside `src/fleet_rlm/daytona/`.
- Keep Runtime Events transport-neutral; `api/sse.py` owns public projection.
- Let `TurnLifecycle.finish()` own Artifact publication and Turn Commit; keep
  terminal ordering and cleanup in `TurnCoordinator`.
- Let Alembic own live schema evolution. Do not add startup `create_all`.
- Do not add `/api/v1`, WebSocket execution, compatibility aliases, or a
  graphical frontend contract.
- Do not hand-edit `openapi.yaml` or
  `tools/fleet-tui/src/generated/openapi.ts`; use `make api-sync`.

## Development workflow

Start with the smallest relevant lane:

```bash
uv run pytest tests/unit/backend/path_to_test.py -q
uv run ruff check path/to/changed.py
uv run ty check src/fleet_rlm
```

For terminal changes:

```bash
pnpm --dir tools/fleet-tui run format:check
pnpm --dir tools/fleet-tui run lint
pnpm --dir tools/fleet-tui run typecheck
pnpm --dir tools/fleet-tui run test
```

Before requesting review, run:

```bash
make check
make check-release
git diff --check
```

Run the focused backend tests for changed behavior and `make api-check` whenever HTTP shapes
may have moved. Credentialed Daytona tests require explicit `FLEET_LIVE=1`;
report live lanes that were intentionally not run.

## Documentation and generated files

- `docs/index.md` is the documentation reachability root.
- `docs/architecture.md` and `docs/reference/codebase-map.md` describe current
  ownership.
- `scripts/README.md` inventories supported top-level helpers.
- Completed plans belong in Git history or the ignored local `.scratch/archive/`,
  not in the active tracked documentation tree.
- `make api-sync` regenerates both public OpenAPI and generated TUI HTTP types;
  `make api-check` verifies them together.

## Submitting changes

Use focused conventional commits such as `feat:`, `fix:`, `refactor:`, `test:`,
`docs:`, or `chore:`. Pull requests should describe before/after behavior, list
validation results, and call out any credentialed evidence still outstanding.

Report vulnerabilities through `SECURITY.md`, not a public issue.
