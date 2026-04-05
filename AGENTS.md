# Repository Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents working in `fleet-rlm-dspy`.
Use it for repo-wide operating rules, shared contracts, and top-level validation.

Read in this order before editing code:

1. This root file for repo-wide workflow and contract rules
2. [src/fleet_rlm/AGENTS.md](src/fleet_rlm/AGENTS.md) for backend/runtime work
3. [src/frontend/AGENTS.md](src/frontend/AGENTS.md) for frontend/UI work

When instructions conflict, the closest `AGENTS.md` to the files you are editing wins.

## Agent Quickstart

Before making changes:

- Inspect the source-of-truth files for the area you are touching.
- Match existing structure and ownership boundaries before introducing new files.
- Assume the working tree may already contain user changes; do not revert or overwrite unrelated edits.
- Prefer the smallest change that preserves the shared backend/frontend contract.
- Run the matching validation lane after edits; do not stop at static inspection.

Source-of-truth files for shared workflow:

- `Makefile` for canonical validation and release targets
- `pyproject.toml` for Python tooling, dependencies, and published CLI entrypoints
- `src/frontend/package.json` for frontend scripts and validation
- `openapi.yaml` for the shared HTTP contract
- `scripts/check_agents_md_freshness.py` for AGENTS consistency checks

Generated or synced artifacts to treat carefully:

- `openapi.yaml` is generated from backend route/schema metadata via `uv run python scripts/openapi_tools.py generate`
- `src/frontend/src/lib/rlm-api/generated/openapi.ts` is generated; do not hand-edit it
- `src/frontend/openapi/fleet-rlm.openapi.yaml` is a synced frontend snapshot; keep it aligned through `pnpm run api:sync` / `pnpm run api:check`
- `src/frontend/dist/` and `src/fleet_rlm/ui/dist/` are build artifacts, not handwritten source

## Tooling Defaults

- Python and package management: `uv`
- Frontend package management: `pnpm`
- Frontend build/lint/format runtime: Vite+ (`vp`) via `pnpm run ...`

This repo explicitly does not use the global `bun` default for frontend work.
Inside `src/frontend`, use `pnpm install --frozen-lockfile` and `pnpm run ...`.

## Repo Map and Ownership

Top-level areas:

- `src/fleet_rlm/`: backend package, CLI, runtime logic, integrations, packaged UI assets
- `src/frontend/`: React app, routes, shell, workspace UI, websocket/API client
- `tests/unit/`, `tests/ui/`, `tests/integration/`, `tests/e2e/`: automated test suites
- `scripts/`: release, metadata, OpenAPI, and docs/AGENTS validation utilities
- `migrations/`: Alembic migrations
- `docs/`: architecture notes, runbooks, and reference material
- `plans/`: durable repo planning and architecture notes
- `.claude/`: local overlays for this checkout only

Ownership expectations:

- Keep backend transport/API concerns in `src/fleet_rlm/api/`
- Keep backend runtime/business logic in `src/fleet_rlm/runtime/` and `src/fleet_rlm/integrations/`
- Keep packaged scaffold assets in `src/fleet_rlm/scaffold/` curated directly; do not auto-sync them from `.claude/`
- Keep frontend route ownership in `src/frontend/src/routes/`
- Keep frontend screen entrypoints thin and push feature internals into `src/frontend/src/app/` and `src/frontend/src/lib/`

## Shared Product Contract

Supported product surfaces:

- `Workbench`
- `Volumes`
- `Optimization`
- `Settings`

Retired `taxonomy`, `skills`, `memory`, and `analytics` routes are intentionally unsupported and should continue to fall through to `/404`.

Shared runtime contract:

- `runtime_mode=modal_chat`: default product path
- `runtime_mode=daytona_pilot`: Daytona-backed variant of the shared ReAct + `dspy.RLM` runtime
- `execution_mode`: Modal-only request option
- Daytona-only request controls: `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`
- Durable mounted-volume roots are `memory/`, `artifacts/`, `buffers/`, and `meta/`
- Session manifests on durable storage live under `meta/workspaces/<workspace_id>/users/<user_id>/react-session-<session_id>.json`

Canonical shared endpoints:

- `/health`
- `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `/api/v1/runtime/*`
- `POST /api/v1/traces/feedback`
- `GET /api/v1/optimization/status`
- `POST /api/v1/optimization/run`
- `/api/v1/ws/chat`
- `/api/v1/ws/execution`

Cross-stack source-of-truth boundaries:

- Backend route mounting, auth/runtime behavior, and UI asset resolution live under `src/fleet_rlm/api/*` and `src/fleet_rlm/api/main.py`
- Frontend route/runtime UX and redirect behavior live under `src/frontend/src/routes/*`
- Live chat and streaming event shaping are shared across:
  - `src/fleet_rlm/api/routers/ws/*`
  - `src/fleet_rlm/runtime/execution/streaming_context.py`
  - `src/frontend/src/lib/rlm-api/*`
  - `src/frontend/src/app/workspace/assistant-content/model/*`
  - `src/frontend/src/lib/workspace/*`

## Agent Operating Rules

- Search for existing ownership boundaries before adding new modules.
- Prefer extending existing service/helper modules over introducing parallel abstractions.
- Keep Modal and Daytona on the same conversational/runtime architecture; Daytona-specific logic belongs in the provider/interpreter layer.
- Treat the Volumes surface as a browser for mounted durable storage only; the live workspace is transient execution state.
- Do not hand-edit generated files or build output unless the task is specifically about generated artifacts.
- If backend request/response shapes or OpenAPI-facing metadata change, regenerate the root spec with `uv run python scripts/openapi_tools.py generate`, update frontend API artifacts, and verify drift with `pnpm run api:check`.
- `fleet web` is the main local app entrypoint and delegates into `fleet-rlm serve-api`.
- Source checkouts prefer `src/frontend/dist` for UI serving; packaged installs fall back to `src/fleet_rlm/ui/dist`.
- Record durable workflow conventions in the appropriate `AGENTS.md` file when you confirm them from code.

Common mistakes to avoid:

- Reintroducing retired routes as first-class surfaces
- Hand-editing generated OpenAPI or route tree files
- Using `bun` or ad-hoc frontend tooling instead of `pnpm` + Vite+
- Moving shared contract logic into screen-only or transport-only layers
- Treating `/ready` or Volumes semantics differently from the implemented runtime contract

## Canonical Commands

Repository setup and shared workflows:

- `uv sync --all-extras`
- `uv run fleet web`
- `uv run fleet-rlm serve-api --port 8000`
- `uv run fleet-rlm serve-mcp --transport stdio`
- `uv run python scripts/openapi_tools.py generate`
- `uv run python scripts/openapi_tools.py validate`
- `make clean`
- `make test-fast`
- `make quality-gate`
- `make release-artifacts`
- `make release-check`

Frontend-only loop:

- `cd src/frontend && pnpm install --frozen-lockfile`
- `pnpm run dev`
- `pnpm run api:check`
- `pnpm run check`

Useful maintenance commands:

- `make metadata-check`
- `uv run python scripts/check_docs_quality.py`
- `uv run python scripts/check_agents_md_freshness.py`

## Validation by Change Type

Choose the smallest lane that matches the files you changed, then escalate if the change crosses contracts.

Mandatory baseline before commit or PR:

- Backend or shared Python edits: `make format`, `make lint`, `make typecheck`
- Frontend edits: `cd src/frontend && pnpm run format`, `pnpm run lint:robustness`, `pnpm run type-check`

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

- `make release-artifacts`
- `make release-check`

## Maintenance Checklist

When updating this repository, keep these aligned:

- `AGENTS.md`, subsystem AGENTS files, and relevant durable docs in `docs/`
- `Makefile`, `pyproject.toml`, and `src/frontend/package.json`
- `openapi.yaml` and generated frontend API artifacts
- Supported route surfaces and runtime-mode behavior across backend and frontend
