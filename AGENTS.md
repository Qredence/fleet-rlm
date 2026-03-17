# Repository Guidelines

## Tooling Defaults

- Python/package management: `uv`
- Frontend/package management for this repo: `pnpm`
- Frontend build runtime: Vite+ (`vp`) under the hood, surfaced through `pnpm run ...`
This repo explicitly overrides the global bun default for frontend work. When you are inside `src/frontend`, use `pnpm install --frozen-lockfile` and `pnpm run ...`.

## Repo Layout

- Backend package: `src/fleet_rlm/`
- Frontend app: `src/frontend/`
- Tests: `tests/unit/`, `tests/ui/`, `tests/integration/`, `tests/e2e/`
- Scripts and release checks: `scripts/`
- Migrations: `migrations/`
- Canonical API contract: `openapi.yaml`
- Long-form docs and runbooks: `docs/`
Use the more specific [src/fleet_rlm/AGENTS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/src/fleet_rlm/AGENTS.md) and [src/frontend/AGENTS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/src/frontend/AGENTS.md) files for subsystem-specific guidance.

## Product and Runtime Contract

- The supported app surfaces are `RLM Workspace`, `Volumes`, and `Settings`.
- Legacy `taxonomy`, `skills`, `memory`, and `analytics` routes redirect to supported pages instead of remaining first-class product surfaces.
- The shared chat runtime supports two top-level modes selected by `runtime_mode`:
  - `modal_chat`: default product path
  - `daytona_pilot`: experimental workbench path
- `execution_mode` is a Modal-only request option.
- Daytona-specific request controls are `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`.
- Frontend and backend must stay aligned on these endpoints:
  - `/health`
  - `/ready`
  - `GET /api/v1/auth/me`
  - `GET /api/v1/sessions/state`
  - `/api/v1/runtime/*`
  - `POST /api/v1/traces/feedback`
  - `/api/v1/ws/chat`
  - `/api/v1/ws/execution`

## Key Architecture Boundaries

- Treat live chat/event rendering as a shared contract across:
  - `src/fleet_rlm/server/routers/ws/*`
  - `src/fleet_rlm/react/streaming_context.py`
  - `src/frontend/src/features/rlm-workspace/*`
  - `src/frontend/src/stores/chatStore.ts`
- Keep Modal and Daytona responsibilities distinct:
  - Modal remains the default chat/runtime path.
  - Daytona remains experimental, but it is integrated into the shared websocket workspace and run-workbench flow.
  - Daytona intentionally uses a custom recursive host-loop runner plus `dspy.Predict`-backed grounding/decomposition/synthesis modules; do not treat it as a `dspy.RLM` wrapper.
- Keep backend source of truth for runtime/auth behavior in `src/fleet_rlm/server/*` and frontend source of truth for route/runtime UX in `src/frontend/src/*`.

## Canonical Commands

- Install everything for local development:
  - `uv sync --all-extras --dev`
- Run the main local Web UI:
  - `uv run fleet web`
- Quick test suite:
  - `make test-fast`
- Run specific test markers:
  - `uv run pytest -q -m "not live_llm and not benchmark"`
  - `uv run pytest -q -m "live_llm"` (requires Modal + configured LM secret)
  - `uv run pytest -q -m "live_daytona"` (requires live Daytona backend)
- Main repo validation:
  - `make quality-gate`
- Full release-oriented validation:
  - `make release-check`
- Frontend-only contributor loop:
  - `cd src/frontend && pnpm install --frozen-lockfile`
  - `pnpm run dev`
  - `pnpm run check`
- Clean stale caches before debugging test failures:
  - `rm -rf .ruff_cache __pycache__ .pytest_cache`

## Validation Lanes

- Docs-only changes:
  - `uv run python scripts/check_docs_quality.py`
  - `uv run python scripts/check_release_hygiene.py`
  - `uv run python scripts/check_release_metadata.py`
- General repo confidence:
  - `make quality-gate`
- Release/pre-publish confidence:
  - `make release-check`

Use subsystem-specific AGENTS files for narrower backend/frontend validation lists when a change only touches one side.

## Notes for Contributors

- `fleet web` is the main entrypoint. It delegates into `fleet-rlm serve-api`.
- Source checkouts prefer `src/frontend/dist` for UI serving; packaged installs fall back to `src/fleet_rlm/ui/dist`.
- Keep docs updated when toolchain, route surfaces, runtime contracts, or contributor workflows change.
