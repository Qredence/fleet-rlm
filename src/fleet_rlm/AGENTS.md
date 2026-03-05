# Repository Guidelines

## Project Structure & Module Organization
From the repo root, core Python code lives in `src/fleet_rlm/` (notably `core/`, `react/`, `server/`, `terminal/`, `models/`, and `analytics/`). Tests are in `tests/` and split by intent: `unit/`, `ui/`, `integration/`, and `e2e/`. Operational scripts (release checks, DB/bootstrap, perf, env validation) are in `scripts/`. The web client is in `src/frontend/`; release/source packaging runs `scripts/build_ui.py` to sync built assets into `src/fleet_rlm/ui/dist`. API contract source is `openapi.yaml`; schema migrations are in `migrations/`.
Within `src/fleet_rlm/core/`, keep host-side adapters (`interpreter.py`, `llm_tools.py`, `volume_ops.py`) conceptually separate from sandbox-side protocol/helpers (`driver.py`, `sandbox_tools.py`, `volume_tools.py`).
For utilities, use `src/fleet_rlm/utils/regex.py` for regex helpers.

## Build, Test, and Development Commands
- `uv sync --extra dev --extra server`: install Python deps for local dev + API work.
- `uv run fleet-rlm --help`: verify CLI entrypoint.
- `make test-fast`: quick default suite (`not live_llm and not benchmark`).
- `make quality-gate`: lint, format check, type check, tests, docs/metadata, frontend checks.
- `make release-check`: full pre-release validation (`clean`, quality, security, build, wheel frontend sync check, twine check).
- Frontend (when touched): `cd src/frontend && bun install --frozen-lockfile && bun run build`.

## Coding Style & Naming Conventions
Use Python 3.10+ with 4-space indentation, explicit type hints, and clear docstrings for non-trivial modules/functions. Enforce style with Ruff and `ty`:
- `uv run ruff format src tests`
- `uv run ruff check src tests`
- `uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"`

Naming conventions: modules/functions/tests use `snake_case`; classes use `PascalCase`; constants use `UPPER_SNAKE_CASE`. Prefer small, focused modules over large mixed-responsibility files.

## Testing Guidelines
Use `pytest` with strict markers (`unit`, `ui`, `integration`, `e2e`, `live_llm`, `benchmark`). Default local run:
- `uv run pytest -q -m "not live_llm and not benchmark"`

Use targeted runs while iterating (for example `uv run pytest -q tests/unit`). Add or update regression tests for every bug fix and keep fixtures deterministic.

## Commit & Pull Request Guidelines
Follow Conventional Commits as seen in history (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`; optional scopes like `feat(react): ...`). Before opening a PR, run `make clean && make quality-gate` (plus `make security-check` for auth/server/runtime-sensitive work). PRs should include a concise summary, linked issue(s) (e.g., `Fixes #123`), commands run with results, and screenshots/GIFs for UI changes. Update docs (`README.md`, `AGENTS.md`, relevant runbooks) when behavior changes.
