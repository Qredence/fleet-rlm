# Repository Guidelines

Fleet RLM combines a Python FastAPI backend, DSPy reasoning, Daytona execution, and a TypeScript terminal client.

## Project Structure & Module Organization

- `src/fleet_rlm/` contains backend packages for sessions, turns, persistence, API, reasoning, workspace, and Daytona.
- `tests/` groups backend tests by behavior owner; shared fixtures live in `tests/support/`.
- `tools/fleet-tui/` contains the terminal client and its tests.
- `config/fleet.toml` owns runtime policy; `migrations/` contains Alembic migrations.
- `docs/` holds guides and references; `scripts/` contains repository helpers. Runtime skill resources live in `src/fleet_rlm/skills/bundled/`.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for ownership boundaries and [TUI guidance](tools/fleet-tui/AGENTS.md) before client changes.

## Build, Test, and Development Commands

Use Python 3.11–3.13, uv, Node 22.19+, and the TUI’s pinned pnpm version.

- `uv sync --all-extras --dev`: install Python dependencies.
- From `tools/fleet-tui/`, run `pnpm install --frozen-lockfile`.
- `make dev`: start the backend; follow [README.md](README.md) for credentials and database initialization.
- `make build`: build Python distributions.
- `make test`: run default non-live backend tests.
- `make tui-check`: check generated contracts, formatting, lint, types, and client tests.
- `make check`: run the primary repository quality gate.

## Coding Style & Naming Conventions

Python uses four-space indentation, double quotes, and 120-character lines. Use `snake_case` for modules/functions and `PascalCase` for classes. Ruff formats and lints; ty checks types.

TypeScript uses two-space indentation, 100-character lines, and Biome. Import directly from owning modules and respect package boundaries.

## Testing Guidelines

Backend tests use pytest and pytest-asyncio; client tests use Vitest. Name files `test_*.py` or `*.test.ts`. Add regressions to existing behavior-owning suites.

Run focused tests with `uv run pytest tests/daytona/test_sandbox_lifecycle.py`. `make test-coverage` enforces 75% package-wide backend coverage.

## Commit & Pull Request Guidelines

Use focused conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, or `chore:`. Follow the PR template: explain resulting behavior, link applicable issues, identify contract/migration impacts, and report exact validation commands and outcomes.

## Validate and Deliver Safely

For documentation changes, run `make check-docs` and `git diff --check`. Keep secrets out of Git. Credentialed provider, Daytona, database, and benchmark runs require explicit operator authorization.

Regenerate contracts with `make api-sync`, `make stream-sync`, or `make config-reference`; never hand-edit generated outputs. Preserve unrelated local changes.
