# Backend Guidelines

## Scope

This file covers the Python/backend side of the repo under `src/fleet_rlm/`.
Use the repo-wide [AGENTS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/AGENTS.md) for shared workflow rules and the frontend-specific [src/frontend/AGENTS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/src/frontend/AGENTS.md) for UI guidance.

## Package Layout

Active top-level areas under `src/fleet_rlm/`:

- `_scaffold/`: bundled skills, agents, teams, and hooks exposed by `fleet-rlm init`
- `api/`: FastAPI app (`main.py`), auth, middleware, routers (HTTP + websocket), schemas, execution event helpers, and server utilities
- `cli/`: Typer CLI surface (`main.py`, `fleet_cli.py`), command modules (`commands/`), and runtime builder constructors (`runners.py`)
- `conf/`: Hydra config defaults
- `core/`: ReAct chat agent, DSPy signatures, runtime modules, execution drivers, streaming context, tool definitions, sandbox helpers, and Modal sandbox runtime
  - `core/agent/`: chat agent, RLM agent, signatures, memory, session history, tool delegation
  - `core/execution/`: interpreter, driver factories, streaming context/citations, document cache/sources, validation
  - `core/models/`: RLM runtime modules, streaming models
  - `core/tools/`: sandbox, filesystem, volume, document, chunking, LLM, memory-intelligence, and delegate tools
- `features/`: domain feature modules
  - `features/analytics/`: PostHog callbacks, MLflow tracing/evaluation/optimization, scorers, sanitization
  - `features/chunking/`: reusable text chunking helpers (headers, JSON keys, size, timestamps)
  - `features/document_ingestion/`: document ingestion pipeline
  - `features/logs/`: execution logging and limits
  - `features/scaffold/`: bundled scaffold skills and templates
  - `features/terminal/`: interactive terminal chat UI, slash-commands, display settings, and Rich-based rendering
- `infrastructure/`: external provider integrations and data access
  - `infrastructure/config/`: environment variable loading (`AppConfig`), runtime settings resolution, and env-var parsing utilities
  - `infrastructure/database/`: Neon/Postgres engine, models, repository layer
  - `infrastructure/mcp/`: FastMCP server surface
  - `infrastructure/providers/daytona/`: experimental Daytona runner, sandbox orchestration, DSPy modules, and websocket chat agent
  - `infrastructure/providers/modal/`: Modal provider adapters
- `ui/`: packaged built frontend assets for installed distributions
- `utils/`: shared helpers including scaffold, regex, Modal, and tool utilities

## CLI Surface

Published scripts from `pyproject.toml`:

- `fleet`: standalone terminal entrypoint, with `fleet web` delegating into `serve-api`
- `fleet-rlm`: full Typer CLI
- `rlm-modal`: alias of `fleet-rlm`

Document and preserve these commands:

- `fleet web`
- `fleet-rlm chat`
- `fleet-rlm serve-api`
- `fleet-rlm serve-mcp`
- `fleet-rlm init`
- `fleet-rlm daytona-smoke`
- `fleet-rlm daytona-rlm`

Important nuance:

- `fleet web` is not a separate backend implementation. It rewrites into `fleet-rlm serve-api --host 0.0.0.0 --port 8000`.
- The `daytona-rlm` CLI still exposes `--max-depth` as a deprecated compatibility flag.
- Daytona websocket requests do not accept request-side `max_depth`; that is enforced by the server schema layer.

## Server and Runtime Contract

Canonical HTTP and websocket surfaces:

- `/health`
- `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `GET/PATCH /api/v1/runtime/settings`
- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`
- `GET /api/v1/runtime/volume/tree`
- `GET /api/v1/runtime/volume/file`
- `POST /api/v1/traces/feedback`
- `/api/v1/ws/chat`
- `/api/v1/ws/execution`
- Optional `/scalar` docs when `scalar_fastapi` is installed

`src/fleet_rlm/api/main.py` is the source of truth for route mounting and SPA asset resolution.

## Runtime Modes and Boundaries

The websocket chat runtime supports two top-level modes:

- `modal_chat`: builds `RLMReActChatAgent`
- `daytona_pilot`: builds `DaytonaWorkbenchChatAgent`

Preserve these boundaries:

- `execution_mode` is Modal-only.
- Daytona request-side source controls are `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`.
- Daytona workbench runs stream through the shared websocket chat surface instead of a separate frontend/runtime stack.
- `src/fleet_rlm/cli/runners.py` is the canonical constructor layer for top-level runtime builders.
- `src/fleet_rlm/api/routers/ws/chat_runtime.py` is the runtime-mode switch point.

## Auth, Persistence, and Analytics

- Supported auth modes are `dev` and `entra`.
- `AUTH_MODE=entra` requires:
  - `AUTH_REQUIRED=true`
  - `DATABASE_REQUIRED=true`
  - `ENTRA_JWKS_URL`
  - `ENTRA_AUDIENCE`
  - an issuer template containing `{tenantid}`
- Entra access is not just token validation. Tenant admission also depends on the repository-backed tenant/user lookup path.
- Runtime settings writes are local-only; `PATCH /api/v1/runtime/settings` is blocked unless `APP_ENV=local`.
- PostHog and MLflow are both active runtime codepaths:
  - PostHog captures runtime/LLM analytics when configured
  - MLflow handles tracing, evaluation, optimization, and feedback workflows

## Backend Architecture Notes

- Keep host-side Modal adapters in `core/` separate from sandbox-side protocol helpers.
- Keep DSPy signatures and runtime modules centralized under `core/agent/signatures.py` and `core/models/rlm_runtime_modules.py`.
- Daytona intentionally uses a custom recursive host-loop runner plus `dspy.Predict`-backed grounding/decomposition/synthesis modules; do not treat it as a `dspy.RLM` wrapper.
- Keep websocket event shaping and session lifecycle inside `api/routers/ws/*`; treat it as a contract with the frontend workspace.
- Use `src/fleet_rlm/utils/regex.py` for regex helpers instead of recreating local helpers.

## Canonical Commands

- Install backend + repo dependencies:
  - `uv sync --all-extras --dev`
- Run the local app:
  - `uv run fleet web`
- Run the API server directly:
  - `uv run fleet-rlm serve-api --port 8000`
- Run the MCP server:
  - `uv run fleet-rlm serve-mcp --transport stdio`
- Run Daytona validation:
  - `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]`
- Run the Daytona pilot:
  - `uv run fleet-rlm daytona-rlm [--repo <url>] [--context-path <path> ...] --task <text> [--batch-concurrency N]`

## Validation

- Fast local confidence:
  - `make test-fast`
- Main repo gate:
  - `make quality-gate`
- Focused backend/runtime coverage:
  - `uv run pytest -q tests/ui/server/test_api_contract_routes.py tests/ui/server/test_router_runtime.py tests/ui/ws/test_chat_stream.py tests/unit/test_ws_chat_helpers.py`
- Daytona-focused backend coverage:
  - `uv run pytest -q tests/unit/test_daytona_rlm_config.py tests/unit/test_daytona_rlm_smoke.py tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_cli.py`

Keep command examples aligned with the Makefile, `pyproject.toml`, and the live router/schema contract.
