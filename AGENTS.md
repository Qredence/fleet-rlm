# Repository Guidelines

Fleet RLM combines a Python FastAPI backend, DSPy reasoning, Daytona execution, and a TypeScript terminal client.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for ownership boundaries and module-level detail, and the [TUI guidance](tools/fleet-tui/AGENTS.md) before client changes. Keep file-level detail out of this guide so it does not drift.

## Layout

- `src/fleet_rlm/`: backend packages for sessions, turns, persistence, API, reasoning, workspace, and Daytona.
- `tests/`: grouped by behavior owner; shared fixtures live in `tests/support/`.
- `tools/fleet-tui/`: terminal client and its tests.
- `config/fleet.toml`: runtime policy; the field-policy inventory is `src/fleet_rlm/config/catalog.py`.
- `migrations/`: Alembic migrations. `docs/` and `scripts/`: guides and repository helpers.
- `src/fleet_rlm/skills/bundled/`: runtime skill resources.

## Invariants

- Child RLM tools are always available, bounded to depth one. Operators do not toggle recursion in TOML.
- Do not add a cross-layer import to make code compile. `make check-dependency-boundaries` enforces the layers.
- Fleet never migrates on startup. After schema changes or pulls, apply migrations per the [database reference](docs/reference/database.md), then run `uv run alembic check`.
- Never hand-edit generated outputs. Regenerate with `make api-sync`, `make stream-sync`, or `make config-reference`. The artifact-to-command table is in [ARCHITECTURE.md](ARCHITECTURE.md#boundaries-and-contracts-for-changes).

## Commands

Use Python 3.11–3.13, uv, Node 22.19+, and the TUI's pinned pnpm version.

- `uv sync --all-extras --dev`: install Python dependencies. From `tools/fleet-tui/`, run `pnpm install --frozen-lockfile`.
- `make dev`: start the backend; follow [README.md](README.md) for credentials and database initialization.
- `make build`: build Python distributions.
- `make test`: default non-live backend tests. `make test-coverage` enforces 75% package-wide coverage.
- `make tui-check`: generated contracts, formatting, lint, types, and client tests.
- `make check-codebase-tree` and `make check-dependency-boundaries`: ownership and layering.
- `make check`: the primary repository quality gate.

## Validation selection

- Docs only: `make check-docs` and `git diff --check`.
- Backend code: the focused suite first, such as `uv run pytest tests/daytona/test_sandbox_lifecycle.py`, then `make check`.
- Client code: `make tui-check`.
- API, stream, or config contracts: regenerate, then `make api-check`, `make stream-check`, or `make check-docs`.
- Schema changes: apply migrations, then `uv run alembic check`.
- Test lanes: see the [testing strategy](docs/how-to-guides/testing-strategy.md).

## Testing

- Backend tests use pytest and pytest-asyncio; client tests use Vitest. Name files `test_*.py` or `*.test.ts`.
- The default lane excludes `live_llm`, `live_daytona`, `benchmark`, `db`, and `packaging`; markers are strict.
- Put tests in the behavior-owning folder under `tests/`, never as a flat `tests/test_*.py`. Outside `tests/live/`, `tests/freeze/`, `tests/e2e/`, and `tests/packaging/`, each file needs at least three cases.
- Add regressions to the existing behavior-owning suite.

## Refactors and cleanup

- When you move, rename, or delete anything, search the old name across `src/`, `tests/`, `docs/`, `scripts/`, `tools/fleet-tui/`, and the instruction files, and update every reference in the same change.
- Delete superseded internal code instead of leaving shims or deprecated aliases. Keep a CLI or HTTP surface only where `openapi.yaml` or a contract test pins it.
- Do not reintroduce retired paths or commands; `scripts/check_repo_hygiene.py` lists them.
- List every new file under `scripts/` in `scripts/README.md`.
- Write scratch and campaign output only to gitignored locations (`.scratch`, `.fleet_rlm`, `.remember`), one artifact per campaign rather than per attempt. Never commit their contents.

## Coding Style

- Python: four-space indentation, double quotes, 120-character lines; `snake_case` modules and functions, `PascalCase` classes. Ruff formats and lints; ty checks types.
- TypeScript: two-space indentation, 100-character lines, Biome. Import directly from owning modules and respect package boundaries.

## Commits and Pull Requests

Use focused conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, or `chore:`. Follow the PR template: explain resulting behavior, link applicable issues, identify contract and migration impacts, and report exact validation commands and outcomes.

## Validate and Deliver Safely

Pick the validation lane from [Validation selection](#validation-selection) and report the exact commands and outcomes.

Keep secrets out of Git. Credentialed provider, Daytona, database, and benchmark runs need explicit operator authorization. That includes anything that spends credentials or credits: `FLEET_LIVE=1` runs, scripts with an explicit run flag, the optional `web_search` tool, and migrations against a shared database. Preserve unrelated local changes.
