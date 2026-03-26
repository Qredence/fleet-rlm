# fleet-rlm: AI coding instructions

## Start here

- Read `AGENTS.md` at the repo root first, then `src/fleet_rlm/AGENTS.md` or `src/frontend/AGENTS.md` depending on the area you touch.
- Treat `Makefile`, `pyproject.toml`, `src/frontend/package.json`, and `openapi.yaml` as source-of-truth workflow files.
- The current backend architecture lives under:
  - `src/fleet_rlm/api/` for FastAPI transport, auth, schemas, and websocket routes
  - `src/fleet_rlm/runtime/` for the shared ReAct + `dspy.RLM` runtime
  - `src/fleet_rlm/integrations/` for database, observability, MCP, and provider backends
  - `src/fleet_rlm/cli/` for `fleet` and `fleet-rlm` entrypoints

## Required workflows

- Use `uv` for Python setup and commands: `uv sync --all-extras --dev`.
- Use `pnpm` in `src/frontend` with `pnpm install --frozen-lockfile`.
- Repo-standard maintenance and validation commands:
  - `make clean`
  - `uv run python scripts/openapi_tools.py generate`
  - `uv run python scripts/openapi_tools.py validate`
  - `make quality-gate`
  - `make release-check`

## Product and contract rules

- Supported product surfaces are `Workbench`, `Volumes`, and `Settings`.
- Retired `taxonomy`, `skills`, `memory`, and `analytics` routes should fall through to `/404`.
- Canonical HTTP/websocket surfaces:
  - `/health`
  - `/ready`
  - `GET /api/v1/auth/me`
  - `GET /api/v1/sessions/state`
  - `/api/v1/runtime/*`
  - `POST /api/v1/traces/feedback`
  - `/api/v1/ws/chat`
  - `/api/v1/ws/execution`
- `/api/v1/ws/chat` is transcript-first; `/api/v1/ws/execution` is the canonical workbench stream.
- Do not hand-edit generated frontend API artifacts in `src/frontend/openapi/` or `src/frontend/src/lib/rlm-api/generated/`.

## OpenAPI and docs sync

- `openapi.yaml` at the repo root is the canonical HTTP contract.
- If backend request/response shapes, route metadata, or OpenAPI-facing schema descriptions change:
  1. Run `uv run python scripts/openapi_tools.py generate`
  2. Run `cd src/frontend && pnpm run api:check`
  3. Update the relevant `README.md`, `AGENTS.md`, and `docs/` pages in the same change

## Runtime notes

- `fleet web` delegates to `fleet-rlm serve-api --host 0.0.0.0 --port 8000`.
- `modal_chat` is the default runtime mode.
- `daytona_pilot` stays on the shared ReAct + `dspy.RLM` backbone; Daytona-specific logic belongs in `src/fleet_rlm/integrations/providers/daytona/`.
- For Modal sandbox work, verify credentials and volume/secret readiness before running live tests.
