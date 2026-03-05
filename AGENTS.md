# Repository Guidelines

## Project Structure & Module Organization
Core Python code lives in `src/fleet_rlm/` (`core/`, `react/`, `server/`, `mcp/`, `analytics/`, `db/`).
Frontend code lives in `src/frontend/` (Vite + React + TypeScript).
Tests are organized by scope in `tests/unit/`, `tests/ui/`, `tests/integration/`, and `tests/e2e/`.
Operational scripts are in `scripts/`; DB migrations are in `migrations/`; API contract source is `openapi.yaml`; longer design/runbook docs are in `docs/`.

## Build, Test, and Development Commands
- `uv sync --all-extras --dev`: install Python dependencies for full local development.
- `uv run fleet web`: run the primary local Web UI experience.
- `make test-fast`: run default pytest suite (`not live_llm and not benchmark`).
- `make quality-gate`: run lint, format check, type check, tests, docs/metadata checks, and frontend checks.
- `make release-check`: full pre-release validation (quality + security + build + wheel checks).
- Frontend-only loop:
  - `cd src/frontend && bun install --frozen-lockfile`
  - `bun run dev` (local UI), `bun run check` (type/lint/tests/build/e2e)

## Coding Style & Naming Conventions
Use Python 3.10+, 4-space indentation, type hints on public functions, and clear docstrings for non-trivial logic.
Enforce style with:
- `uv run ruff format src tests`
- `uv run ruff check src tests`
- `uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"`

Naming: `snake_case` for modules/functions/tests, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants. Keep modules focused and avoid mixing unrelated concerns.

## Testing Guidelines
Use `pytest` with strict markers (`unit`, `ui`, `integration`, `e2e`, `live_llm`, `benchmark`).
Default local run: `uv run pytest -q -m "not live_llm and not benchmark"`.
Name tests `test_<behavior>.py` and add regression tests for bug fixes.
Frontend tests use Vitest (`bun run test:unit`) and Playwright (`bun run test:e2e`).

## Commit & Pull Request Guidelines
Follow Conventional Commits seen in history: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:` (scopes encouraged, e.g., `fix(frontend): ...`).
Before opening a PR, run at least `make quality-gate`.
PRs should include: clear summary, linked issue (`Fixes #123`), type of change, commands run + results, and screenshots/GIFs for UI updates. Update docs (`README.md`, `AGENTS.md`, `docs/`) when behavior changes.

## Security & Configuration Tips
Use `.env.example` as a template; never commit `.env` or secrets. Keep API keys in environment variables/secret managers, not source code. Treat `live_llm` and benchmark tests as opt-in and run them intentionally.
