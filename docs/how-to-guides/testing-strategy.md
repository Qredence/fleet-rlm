# Testing Strategy

This guide documents the fleet-rlm test system, including pytest markers, test organization, and common test commands for both backend and frontend code.

## Test Markers

fleet-rlm uses pytest markers to categorize tests by scope and runtime requirements. All markers are defined in `pyproject.toml` under `[tool.pytest.ini_options]`.

### Backend Markers

| Marker        | Description                                    | Typical Duration               |
| ------------- | ---------------------------------------------- | ------------------------------ |
| `unit`        | Fast unit tests for isolated modules           | Milliseconds                   |
| `ui`          | UI/server tests for API and WebSocket behavior | Seconds                        |
| `integration` | Integration tests across DB/runtime boundaries | Seconds                        |
| `db`          | Database-backed integration tests              | Seconds                        |
| `e2e`         | End-to-end workflow smoke tests                | Seconds to minutes             |
| `benchmark`   | Performance/throughput benchmark tests         | Variable                       |
| `live_llm`    | Tests requiring live Modal + configured LLM    | Variable, requires credentials |

### Marker Usage

**Default Local Development**

The default test command excludes `live_llm` and `benchmark` tests:

```bash
uv run pytest -q -m "not live_llm and not benchmark"
```

This ensures fast feedback during development without requiring:

- Modal credentials
- Configured LLM API keys
- External service connections

**Running Specific Suites**

```bash
# Unit tests only
uv run pytest -q tests/unit -m "not live_llm and not benchmark"

# UI/server tests
uv run pytest -q tests/ui -m "not live_llm and not benchmark"

# Integration tests
uv run pytest -q tests/integration -m "not live_llm and not benchmark"

# E2E tests
uv run pytest -q tests/e2e -m "not live_llm and not benchmark"
```

**Running Live LLM Tests**

Live LLM tests require Modal credentials and configured LLM secrets:

```bash
# Ensure Modal is authenticated
uv run modal profile current

# Run live LLM tests
uv run pytest -q -m "live_llm"
```

**Running Benchmarks**

Benchmark tests measure performance and throughput:

```bash
uv run pytest -q -m "benchmark"
```

## Test Directory Structure

Tests are organized by scope in the `tests/` directory:

```text
tests/
├── conftest.py           # Shared fixtures and marker registration
├── unit/                 # Fast unit tests for isolated modules
├── ui/                   # API + WebSocket behavior tests
│   ├── conftest.py       # UI/server fixture boundaries
│   ├── server/           # HTTP API contract tests
│   └── ws/               # WebSocket tests
├── integration/          # Integration tests across DB/runtime
│   └── conftest.py       # Integration runtime gates + DB fixtures
└── e2e/                  # End-to-end workflow smoke tests
```

### Directory Guidelines

| Directory            | Purpose                                          | Dependencies        |
| -------------------- | ------------------------------------------------ | ------------------- |
| `tests/unit/`        | Isolated module tests, no external services      | None                |
| `tests/ui/`          | API routes, WebSocket endpoints, server behavior | FastAPI test client |
| `tests/integration/` | Cross-boundary tests, database operations        | Database, runtime   |
| `tests/e2e/`         | Full workflow smoke tests                        | Full stack          |

### Fixture Organization

- **Root fixtures**: `tests/conftest.py` contains shared suite fixtures
- **UI fixtures**: `tests/ui/conftest.py` defines UI/server fixture boundaries
- **WebSocket fixtures**: `tests/ui/ws/` contains WebSocket test fakes
- **Integration fixtures**: `tests/integration/conftest.py` has DB fixtures

> **Best Practice:** Consolidate related WebSocket behavior into `tests/ui/ws/test_chat_stream.py` and HTTP contract checks into `tests/ui/server/test_api_contract_routes.py` instead of creating many tiny route-specific files.

## Backend Test Commands

### Makefile Targets

The `Makefile` provides convenient targets for running tests:

| Command                 | Description                                                    |
| ----------------------- | -------------------------------------------------------------- |
| `make test-fast`        | Run default test suite (excludes `live_llm` and `benchmark`)   |
| `make test-unit`        | Run unit tests only                                            |
| `make test-ui`          | Run UI/server tests only                                       |
| `make test-integration` | Run integration + e2e tests                                    |
| `make quality-gate`     | Run backend lint/type/tests, metadata/docs checks, and the repo frontend gate |
| `make release-check`    | Run release-oriented validation, including security and packaging |

### Direct pytest Commands

```bash
# Default fast test suite
uv run pytest -q -m "not live_llm and not benchmark"

# Verbose output
uv run pytest -v -m "not live_llm and not benchmark"

# Specific test file
uv run pytest -q tests/unit/test_example.py

# Specific test function
uv run pytest -q tests/unit/test_example.py::test_function_name

# With coverage
uv run pytest -q --cov=src/fleet_rlm -m "not live_llm and not benchmark"
```

## Frontend Test Commands

Frontend tests use **Vitest** for unit tests and **Playwright** for end-to-end tests. All frontend commands run from `src/frontend/`.

### Package.json Scripts

| Command                   | Description                                                       |
| ------------------------- | ----------------------------------------------------------------- |
| `pnpm run api:check`      | Verify committed frontend OpenAPI artifacts match the backend spec |
| `pnpm run type-check`     | Run TypeScript type checks                                        |
| `pnpm run lint:robustness`| Run the repo lint lane                                            |
| `pnpm run test:unit`      | Run Vitest unit tests                                             |
| `pnpm run test:e2e`       | Run Playwright end-to-end tests                                   |
| `pnpm run test:watch`     | Run Vitest in watch mode                                          |
| `pnpm run test:coverage`  | Run Vitest with coverage report                                   |
| `pnpm run check`          | Run type-check, lint, unit tests, build, and e2e tests            |

### Running Frontend Tests

```bash
# From repository root
cd src/frontend

# Run unit tests
pnpm run test:unit

# Run e2e tests
pnpm run test:e2e

# Run repo-aligned frontend checks
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build

# Run the full frontend suite (adds Playwright e2e)
pnpm run check

# Watch mode for development
pnpm run test:watch
```

### Frontend Test Organization

```text
src/frontend/src/
├── __tests__/            # Shared test utilities
├── features/
│   └── rlm-workspace/
│       └── __tests__/    # Feature-specific unit tests
└── ...
```

## Quality Gates

### Full Quality Gate

Run all checks before submitting a PR:

```bash
make quality-gate
```

This runs:

1. `lint` - Ruff linting
2. `format-check` - Ruff format verification
3. `typecheck` - Type checking with ty
4. `test-fast` - Default test suite
5. `metadata-check` - Release metadata and hygiene validation
6. `docs-check` - Markdown/docs quality validation
7. `frontend-check` - Frontend OpenAPI sync check, type-check, lint, unit tests, and build

### Release Gate

Run the release-oriented validation lane before tagging:

```bash
make release-check
```

This extends `make quality-gate` with:

1. security checks (`pip-audit`, `bandit`)
2. frontend UI build sync
3. wheel/sdist build validation
4. `twine check` for publishability

### Pre-commit Hooks

Install pre-commit hooks for automatic checks:

```bash
uv run pre-commit install
```

Run pre-commit manually:

```bash
uv run pre-commit run --all-files
```

## Test Anti-Patterns

Avoid these common anti-patterns when writing tests:

### Backend Anti-Patterns

- **Duplicating debug-auth header constants** across test files
- **Rebuilding `create_app()` boilerplate** inside every test module
- **Hidden startup side effects** in tests (analytics/network calls)
- **Embedding shared fake agent logic** directly in individual test files

### Recommended Patterns

- Use fixtures from `conftest.py` for shared test setup
- Use the `auth_headers` fixture for API route tests
- Use the `websocket_auth_headers` fixture for WebSocket tests
- Consolidate related tests in appropriate suite files

## CI/CD Integration

### Default CI Pipeline

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: uv run pytest -q -m "not live_llm and not benchmark"
```

### Live LLM Tests in CI

Live LLM tests require secrets configuration:

```yaml
- name: Run live LLM tests
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  env:
    MODAL_TOKEN_ID: ${{ secrets.MODAL_TOKEN_ID }}
    MODAL_TOKEN_SECRET: ${{ secrets.MODAL_TOKEN_SECRET }}
    DSPY_LM_MODEL: ${{ secrets.DSPY_LM_MODEL }}
    DSPY_LM_API_KEY: ${{ secrets.DSPY_LM_API_KEY }}
  run: uv run pytest -q -m "live_llm"
```

## Related Documentation

- [Developer Setup Guide](developer-setup.md) - Setting up a local development environment
- [CONTRIBUTING.md](../../CONTRIBUTING.md) - Contribution guidelines
- [AGENTS.md](../../AGENTS.md) - Project architecture and conventions
