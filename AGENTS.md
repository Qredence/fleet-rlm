# Repository Agent Instructions

## Project Overview

`fleet-rlm` is a Web UI-first adaptive recursive language model workspace built around a Daytona-backed recursive DSPy runtime. It layers a thin FastAPI/WebSocket transport shell and a narrow Microsoft Agent Framework outer host over a core recursive worker/runtime that executes tasks in Daytona sandboxes.

The repo ships as a single Python package (`fleet-rlm`) with a bundled React frontend. Published installs include built frontend assets, so end users do not need a separate frontend build step.

Supported product surfaces:

- `Workbench` — adaptive chat and runtime execution
- `Volumes` — browser for mounted durable storage
- `Optimization` — DSPy evaluation and optimization workflows
- `Settings` — runtime configuration and diagnostics

Retired `taxonomy`, `skills`, `memory`, and `analytics` routes are intentionally unsupported and should continue to fall through to `/404`.

## Technology Stack

### Backend

- **Language**: Python >= 3.10 (3.10, 3.11, 3.12 supported)
- **Package manager**: `uv`
- **Web framework**: FastAPI 0.135.3 with WebSocket support
- **Runtime core**: DSPy 3.1.3 + recursive ReAct / `dspy.RLM` workbench agent
- **Sandbox provider**: Daytona (only supported runtime substrate)
- **Persistence**: SQLModel + SQLAlchemy with asyncpg/psycopg; SQLite sidecar for local sessions
- **Auth**: `dev` mode or Entra (Azure AD) bearer-token validation
- **Observability**: PostHog, MLflow
- **CLI**: Typer + argparse (`fleet` and `fleet-rlm` entrypoints)
- **MCP**: Optional FastMCP server surface

### Frontend

- **Framework**: React 19 + TypeScript 5.9+
- **Router**: TanStack Router (file-based routes)
- **State**: Zustand + TanStack Query
- **Build tool**: Vite+ (`vp` CLI) — not plain Vite
- **Package manager**: `pnpm` 10.32.1 (explicitly required; do not use `bun`)
- **Styling**: Tailwind CSS v4 + `tw-animate-css` + shadcn/Base UI primitives
- **Testing**: Vitest (unit), Playwright (e2e)
- **Markdown rendering**: `streamdown` + Shiki

## Architecture and Code Organization

The backend is organized into four layers, innermost first:

1. **Worker / Runtime Core** — recursive chat/runtime logic
   - `src/fleet_rlm/worker/` — workspace task stream boundary, streaming contracts
   - `src/fleet_rlm/runtime/agent/` — main cognition loop (`chat_agent.py`, `recursive_runtime.py`, DSPy signatures)
   - `src/fleet_rlm/runtime/execution/` — execution drivers and event assembly
   - `src/fleet_rlm/runtime/models/` — runtime model construction and registry
   - `src/fleet_rlm/runtime/content/` — content-oriented helpers
   - `src/fleet_rlm/runtime/tools/` — grouped tool helpers (content, sandbox, filesystem, batch, LLM)
   - `src/fleet_rlm/runtime/quality/` — offline DSPy evaluation, GEPA optimization, datasets, scoring, module registry

2. **Daytona Substrate** — sandbox and durable storage
   - `src/fleet_rlm/integrations/daytona/` — interpreter lifecycle, runtime, volumes, diagnostics
   - `src/fleet_rlm/integrations/database/` — async Neon/Postgres persistence (`FleetRepository`)
   - `src/fleet_rlm/integrations/local_store.py` — lightweight SQLite sidecar

3. **Agent Framework Outer Host** — hosted policy layer
   - `src/fleet_rlm/agent_host/` — workflow, HITL checkpointing, terminal flow, sessions, execution events, REPL bridge

4. **Transport Shell** — auth, routing, websockets, SPA serving
   - `src/fleet_rlm/api/main.py` — app factory, lifespan, route mounting, SPA asset resolution
   - `src/fleet_rlm/api/bootstrap.py` — runtime bootstrap, LM loading, persistence init
   - `src/fleet_rlm/api/routers/` — HTTP routers and websocket endpoints
   - `src/fleet_rlm/api/runtime_services/` — thin orchestration for chat runtime, persistence, diagnostics, settings, volumes
   - `src/fleet_rlm/api/events/` — execution event shaping
   - `src/fleet_rlm/api/schemas/` — request/response schemas
   - `src/fleet_rlm/api/auth/` — auth derivatives for HTTP and websocket identity

Other backend areas:

- `src/fleet_rlm/cli/` — `fleet` and `fleet-rlm` CLI entrypoints, commands, terminal UI
- `src/fleet_rlm/integrations/mcp/` — MCP integration
- `src/fleet_rlm/integrations/observability/` — PostHog and MLflow wiring
- `src/fleet_rlm/utils/` — shared helpers (e.g., `utils/regex.py`)
- `src/fleet_rlm/scaffold/` — curated packaged Claude Code translation assets (exposed by `fleet-rlm init`)
- `src/fleet_rlm/ui/dist/` — **generated** bundled frontend assets for Python package distributions

Frontend organization:

- `src/frontend/src/routes/` — file-based route definitions
- `src/frontend/src/features/layout/` — app chrome, root layout
- `src/frontend/src/features/workspace/` — workbench surface (screen, conversation, composer, inspection, workbench, session)
- `src/frontend/src/features/volumes/` — volumes surface
- `src/frontend/src/features/settings/` — settings surface
- `src/frontend/src/features/optimization/` — optimization surface
- `src/frontend/src/components/ui/` — shadcn/Base UI primitives
- `src/frontend/src/components/ai-elements/` — AI Elements registry components
- `src/frontend/src/components/product/` — reusable product compositions
- `src/frontend/src/lib/rlm-api/` — REST/websocket clients and generated OpenAPI types
- `src/frontend/src/lib/workspace/` — backend event adapters, chat stores, frame shaping
- `src/frontend/src/stores/` — cross-app shell/layout and navigation state
- `src/frontend/src/styles/globals.css` — Tailwind v4 theme baseline and tokens

## Build and Test Commands

### Repository setup

```bash
uv sync --all-extras          # install Python deps with all extras
uv sync --extra dev           # install with dev extras only
```

### Running the application

```bash
uv run fleet web              # start Web UI + API server (delegates to fleet-rlm serve-api)
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm serve-mcp --transport stdio
uv run fleet-rlm chat --trace-mode compact
```

### Backend validation

```bash
make format                   # ruff format src tests
make format-check             # ruff format --check
make lint                     # ruff check
make typecheck                # ty check src (excludes scaffold)
make test                     # pytest excluding live_llm and benchmark
make test-unit                # unit tests only
make test-ui                  # UI/server tests only
make test-integration         # integration + e2e tests
make check                    # lint + format-check + typecheck + test + check-release + check-docs + check-frontend
make quality-gate             # alias for make check
make check-release            # release hygiene, metadata, and AGENTS.md freshness
make check-docs               # docs quality checks
make check-security           # pip-audit + bandit
make check-deps               # deptry (backend) + knip (frontend)
make clean                    # remove caches and local artifacts
```

### Frontend validation (from `src/frontend/`)

```bash
pnpm install --frozen-lockfile
pnpm run dev                  # start Vite+ dev server (proxies /api/v1 to localhost:8000)
pnpm run build                # production build
pnpm run type-check           # tsc --noEmit
pnpm run lint                 # vp lint
pnpm run lint:robustness      # alias for lint
pnpm run format               # vp fmt
pnpm run format:check         # vp fmt --check
pnpm run test:unit            # vitest run
pnpm run test:watch           # vitest
pnpm run test:coverage        # vitest run --coverage
pnpm run test:e2e             # playwright test
pnpm run api:sync             # sync openapi spec + regenerate types
pnpm run api:check            # verify frontend OpenAPI artifacts are in sync
pnpm run check                # type-check + lint + test:unit + build + test:e2e
```

### Release

```bash
make build-ui                 # build frontend and sync to src/fleet_rlm/ui/dist
make build-release            # build-ui + build Python wheels + validate
make release                  # clean + check + security + build-release
```

## Code Style Guidelines

### Python

- **Formatter / Linter**: `ruff` (enforced in CI and pre-commit)
- **Type checker**: `ty` (not mypy)
- **Docstrings**: required for modules and public functions
- **Type hints**: required on all function signatures
- **Import rules**: config/package-root modules must not have import-time side effects (no DSPy, provider SDKs, MLflow runtime helpers, or PostHog callbacks at import time)
- **Layering**: keep transport logic in `api/`, business logic in `runtime/` or `src/fleet_rlm/integrations/daytona/`, and hosted policy in `agent_host/`
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:`

### Frontend

- **Formatter / Linter**: Vite+ built-in (`vp lint` / `vp fmt`) with oxc, typescript, unicorn, react plugins
- **Naming**: `kebab-case` for new handwritten feature files; `PascalCase` for React components; `useThing` for hooks
- **Ref passing**: prefer React 19 direct ref passing over `forwardRef`
- **Styling**: use Tailwind semantic tokens and shared variants; avoid arbitrary colors or local token layers
- **Import boundaries** (enforced by lint rules):
  - `src/components/ui/*`, `src/components/ai-elements/*`, `src/components/product/*` must not import from `src/screens/*`
  - `src/lib/workspace/*` must not depend on workspace UI modules
  - `src/features/layout/*` must import workspace/volumes through top-level feature contracts only
- **Canonical `cn()` import path**: `@/lib/utils`

## Testing Instructions

### Backend

- **Framework**: `pytest` with `pytest-asyncio` and `pytest-timeout`
- **Test directories**:
  - `tests/unit/` — unit tests
  - `tests/ui/` — UI/server tests
  - `tests/integration/` — integration tests
  - `tests/e2e/` — backend e2e tests
- **Markers**:
  - `unit` — unit test suite
  - `ui` — UI/server test suite
  - `integration` — integration test suite
  - `db` — database-backed integration tests
  - `e2e` — end-to-end test suite
  - `benchmark` — performance tests
  - `live_llm` — requires a live configured LM / LiteLLM backend
  - `live_daytona` — requires a live Daytona backend and explicit opt-in
- **Fast lane** (default): excludes `live_llm` and `benchmark`
- **Coverage target**: >80% on new features

### Frontend

- **Unit**: Vitest with jsdom environment; tests live in `src/**/__tests__/` or as `*.test.{ts,tsx}`
- **E2E**: Playwright with Chromium; tests live in `tests/e2e/`
- **Coverage thresholds** (v8 provider):
  - lines: 60%, functions: 60%, branches: 50%, statements: 60%
- **Exclusions from coverage**: `main.tsx`, `*.d.ts`, `test/`, `__tests__/`, generated OpenAPI types, shadcn primitives

### CI

GitHub Actions runs on push to `main`/`master` and on PRs:

1. **Quality** — release hygiene, docs quality, AGENTS.md freshness, security (pip-audit + bandit), deptry, ruff, ty
2. **Test Unit** — pytest unit tests (non-live, 120s timeout)
3. **Test UI** — pytest UI tests (non-live, 120s timeout)
4. **Test Integration** — pytest integration + e2e (non-live)
5. **Frontend Check** — pnpm install, knip, Vite+ check, vitest, build

## Security Considerations

- **Supported versions**: 0.4.x and later
- **Vulnerability reporting**: email `contact@qredence.ai` — do **not** open public GitHub issues for security vulnerabilities
- **Static analysis**:
  - `bandit` runs on `src/fleet_rlm` (excluding `tests` and `scaffold`)
  - `pip-audit` scans for known vulnerabilities (currently ignores GHSA-5239-wwwm-4pmq until Pygments patches it)
- **Auth modes**:
  - `dev` — permissive local development mode
  - `entra` — real Entra bearer-token validation plus Neon-backed tenant admission
- **Settings protection**: `PATCH /api/v1/runtime/settings` is blocked unless `APP_ENV=local`
- **Secrets**: use `.env` for local development (never commit it); in production use Modal secrets or equivalent

## Generated and Synced Artifacts

Do **not** hand-edit the following files:

- `openapi.yaml` — generated from backend route/schema metadata via `uv run python scripts/openapi_tools.py generate`
- `src/frontend/src/lib/rlm-api/generated/openapi.ts` — generated from OpenAPI spec
- `src/frontend/openapi/fleet-rlm.openapi.yaml` — synced frontend snapshot of the root spec
- `src/frontend/src/routeTree.gen.ts` — generated by TanStack Router
- `src/frontend/dist/` — built frontend output
- `src/fleet_rlm/ui/dist/` — packaged UI assets copied from frontend build

When backend request/response shapes or OpenAPI-facing metadata change:

1. Regenerate root spec: `uv run python scripts/openapi_tools.py generate`
2. Sync frontend artifacts: `cd src/frontend && pnpm run api:sync`
3. Verify no drift: `pnpm run api:check`

## Runtime Contract

The shared backend/frontend runtime contract is **Daytona-only**:

- `execution_mode` is a per-turn execution hint.
- Daytona request controls: `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`
- Durable mounted-volume roots: `memory/`, `artifacts/`, `buffers/`, `meta/`
- Session manifests on durable storage: `meta/workspaces/<workspace_id>/users/<user_id>/react-session-<session_id>.json`
- Daytona idle lifecycle timers: `auto_stop_interval=30` (minutes), `auto_archive_interval=60` (minutes)

Canonical websocket surfaces:

- `/api/v1/ws/execution` — conversational websocket stream (auth + frames; rejects query `session_id`)
- `/api/v1/ws/execution/events` — passive execution/workbench event stream (requires query `session_id`; no message/command frames)

Canonical HTTP surfaces (non-exhaustive):

- `/health`, `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/*` — session CRUD and turns
- `GET/PATCH /api/v1/runtime/settings`
- `POST /api/v1/runtime/tests/daytona`, `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`, `GET /api/v1/runtime/volume/*`
- `GET/POST /api/v1/optimization/*` — optimization status, runs, datasets, modules, results, compare
- `POST /api/v1/traces/feedback`

## Validation by Change Type

Choose the smallest lane that matches your change, then escalate if it crosses contracts.

### Backend or shared Python edits

Mandatory baseline:

```bash
make format
make lint
make typecheck
```

Full confidence:

```bash
make test
make check
```

### Frontend-only edits

```bash
cd src/frontend
pnpm install --frozen-lockfile
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build
```

Full confidence:

```bash
pnpm run check
```

### Backend or shared contract changes

```bash
make test
make check
```

### Release-oriented confidence

```bash
make build-release
make release
```

## Maintenance Checklist

When updating this repository, keep these aligned:

- `AGENTS.md`, subsystem AGENTS files (`src/fleet_rlm/AGENTS.md`, `src/frontend/AGENTS.md`), and relevant durable docs in `docs/`
- `Makefile`, `pyproject.toml`, and `src/frontend/package.json`
- `openapi.yaml` and generated frontend API artifacts
- Supported route surfaces and Daytona-only runtime behavior across backend and frontend

## Reading Order for Agents

Before making changes, read in this order:

1. This root `AGENTS.md` for repo-wide workflow and contract rules
2. `src/fleet_rlm/AGENTS.md` for backend/runtime work
3. `src/frontend/AGENTS.md` for frontend/UI work

When instructions conflict, the closest `AGENTS.md` to the files you are editing wins.

Backend reading order for understanding the runtime story:

1. `src/fleet_rlm/api/main.py`
2. `src/fleet_rlm/api/routers/ws/endpoint.py`
3. `src/fleet_rlm/agent_host/workflow.py`
4. `src/fleet_rlm/worker/streaming.py`
5. `src/fleet_rlm/runtime/factory.py`
6. `src/fleet_rlm/runtime/agent/chat_agent.py`
7. `src/fleet_rlm/integrations/daytona/interpreter.py`
8. `src/fleet_rlm/integrations/daytona/runtime.py`
