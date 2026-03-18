# Repository Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents working in `fleet-rlm-dspy`.
Use it for repo-wide operating rules, cross-stack contracts, and top-level validation.

Start here, then load the more specific guide for the area you are modifying:

- Backend: [src/fleet_rlm/AGENTS.md](src/fleet_rlm/AGENTS.md)
- Frontend: [src/frontend/AGENTS.md](src/frontend/AGENTS.md)

Source-of-truth files for shared workflow:

- `Makefile` for canonical validation and release targets
- `pyproject.toml` for Python tooling, dependencies, and published CLI entrypoints
- `src/frontend/package.json` for frontend scripts and validation
- `openapi.yaml` for the HTTP contract shared by backend and frontend

## Agent Priorities

- Read the root file before making shared workflow or cross-stack changes.
- Defer to subsystem AGENTS files once work becomes backend- or frontend-specific.
- Treat `Makefile`, `pyproject.toml`, `src/frontend/package.json`, and `openapi.yaml` as source of truth when docs drift from code.
- Prefer the smallest validation lane that matches the change, but escalate to `make quality-gate` for shared-contract work.
- Update AGENTS/docs when you discover a stable workflow or when your change alters repo conventions.

## Tooling Defaults

- Python and package management: `uv`
- Frontend package management in this repo: `pnpm`
- Frontend build runtime: Vite+ (`vp`) surfaced through `pnpm run ...`

This repo explicitly overrides the global `bun` default for frontend work.
When operating inside `src/frontend`, use `pnpm install --frozen-lockfile` and `pnpm run ...`.

## Repo Map

- `src/fleet_rlm/`: backend package, CLI, FastAPI server, runtime logic, providers
- `src/frontend/`: React app, route tree, websocket client, workspace UI
- `tests/unit/`, `tests/ui/`, `tests/integration/`, `tests/e2e/`: automated test suites
- `scripts/`: release, docs, and repository validation utilities
- `migrations/`: Alembic migrations
- `docs/`: architecture, reference docs, runbooks, and how-to guides
- `plans/`: long-form planning and architecture review documents
- `.claude/`: local agent, hook, MCP, and skill overlays used in this checkout
- `.mcp.json`: workspace MCP configuration

## Shared Product Contract

Supported app surfaces:

- `RLM Workspace`
- `Volumes`
- `Settings`

Legacy `taxonomy`, `skills`, `memory`, and `analytics` routes remain in the route tree only as redirects. Do not reintroduce them as first-class product surfaces without updating the backend/frontend contract and docs.

Shared runtime contract:

- `runtime_mode=modal_chat`: default product path
- `runtime_mode=daytona_pilot`: experimental workbench path
- `execution_mode`: Modal-only request option
- Daytona-only request controls: `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`

Canonical shared endpoints:

- `/health`
- `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `/api/v1/runtime/*`
- `POST /api/v1/traces/feedback`
- `/api/v1/ws/chat`
- `/api/v1/ws/execution`

Cross-stack source-of-truth boundaries:

- Backend route mounting, auth/runtime behavior, and UI asset resolution live under `src/fleet_rlm/api/*` and `src/fleet_rlm/api/main.py`
- Frontend route/runtime UX and redirect behavior live under `src/frontend/src/routes/*`
- Live chat and streaming event shaping are a shared contract across:
  - `src/fleet_rlm/api/routers/ws/*`
  - `src/fleet_rlm/core/execution/streaming_context.py`
  - `src/frontend/src/features/rlm-workspace/*`
  - `src/frontend/src/stores/chatStore.ts`

## Agent Operating Rules

- Keep Modal and Daytona responsibilities distinct. Daytona is integrated into the shared workspace flow, but it is still experimental and should not be treated as a generic `dspy.RLM` wrapper.
- Daytona intentionally uses a custom recursive host-loop runner plus `dspy.Predict`-backed grounding/decomposition/synthesis modules; do not treat it as a `dspy.RLM` wrapper.
- Treat `openapi.yaml` as the canonical API contract. If you change backend request/response shapes or routes, update generated frontend API artifacts and verify drift with `pnpm run api:check`.
- `fleet web` is the main local app entrypoint. It delegates into `fleet-rlm serve-api`.
- Source checkouts prefer `src/frontend/dist` for UI serving. Packaged installs fall back to `src/fleet_rlm/ui/dist`.
- Keep docs in sync when tooling, routes, runtime contracts, or agent workflow change. If you learn a stable repo convention, record it here or in the subsystem AGENTS file instead of leaving it only in chat.
- Prefer `plans/` for durable repo-level planning and architecture notes. Use `src/frontend/PLANS.md` for frontend-specific execution plans.

## Canonical Commands

Repository setup and shared workflows:

- `uv sync --all-extras --dev`
- `uv run fleet web`
- `uv run fleet-rlm serve-api --port 8000`
- `uv run fleet-rlm serve-mcp --transport stdio`
- `make test-fast`
- `make quality-gate`
- `make release-check`

Frontend-only agent loop:

- `cd src/frontend && pnpm install --frozen-lockfile`
- `pnpm run dev`
- `pnpm run api:check`
- `pnpm run check`

Useful maintenance commands:

- `make metadata-check`
- `uv run python scripts/check_agents_md_freshness.py`
- `rm -rf .ruff_cache __pycache__ .pytest_cache`

## Validation by Change Type

Choose the smallest lane that gives confidence for the files you touched.
Use subsystem-specific AGENTS files for narrower backend/frontend test lists and command recommendations.

Docs-only changes:

- `uv run python scripts/check_docs_quality.py`
- `make metadata-check`

Backend or shared contract changes:

- `make test-fast`
- `make quality-gate`

Frontend-only changes:

- `cd src/frontend && pnpm install --frozen-lockfile`
- `pnpm run api:check`
- `pnpm run type-check`
- `pnpm run lint:robustness`
- `pnpm run test:unit`
- `pnpm run build`

Release-oriented confidence:

- `make release-check`

## Maintenance Checklist

When updating this repository, make sure the following stay aligned:

- `AGENTS.md`, subsystem AGENTS files, and `docs/` guidance
- `Makefile`, `pyproject.toml`, and `src/frontend/package.json`
- `openapi.yaml` and generated frontend API types
- Runtime-mode docs and the actual websocket/router implementation
