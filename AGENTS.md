# Repository Guidelines

Fleet RLM Lite is a Python application with a Rich CLI, FastAPI SSE server,
DSPy engine, Daytona execution, PostgreSQL VFS, and MLflow/GEPA evaluation.

## Project layout

- `src/fleet_rlm/` contains the active application: `config.py`, `skills.py`,
  `session.py`, `cli.py`, and the `api/` (FastAPI routers), `engine/` (routes,
  signatures, Turn program),
  `sandbox/` (Daytona), `store/` (PostgreSQL), and `optimize/`
  (datasets, scorers, GEPA) packages.
- `tests/` contains the Lite test suite.
- `docs/` contains product and developer documentation.
- `.fleet/skills/` is for user Skills. `.agents/skills/` is reserved for
  developer agent skills and must not be configured as user Skills sync.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing runtime boundaries.
Do not document removed API, TUI, migration, or configuration capabilities as
current behavior.

## Build, test, and development

Use Python 3.11–3.13 and uv. Common repository targets are defined in
`Makefile`:

- `make install-dev`: install development dependencies.
- `make serve`: run the FastAPI server.
- `make cli`: display CLI help.
- `make test`: run pytest.
- `make lint`, `make format-check`, `make typecheck`: static checks.
- `make api-check`: verify the generated OpenAPI document.
- `make check`: run the complete non-live quality gate and build.

Use `uv run pytest <focused-test>` for focused backend validation. Live Daytona,
model-provider, database, and evaluation runs require explicit authorization
and appropriate external resources; ordinary CI must not depend on them.

## Style and change practice

Python uses four-space indentation and Ruff formatting/linting. Keep changes
focused, add regression coverage for behavior changes, and update documentation
when public behavior changes. Never commit credentials or `.env` files.

Use conventional commit prefixes (`feat:`, `fix:`, `refactor:`, `test:`,
`docs:`, `chore:`). Do not commit, push, publish, or deploy unless explicitly
authorized.

## Validation

For docs changes, run `make check-docs` when available and always run
`git diff --check`. `make check` is the primary code-quality gate. Do not run
credentialed integrations without authorization.
