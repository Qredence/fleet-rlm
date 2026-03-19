# Backend Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents modifying the backend under `src/fleet_rlm/`.
Read the root [AGENTS.md](../../AGENTS.md) first for shared repo rules.
Only consult [src/frontend/AGENTS.md](../frontend/AGENTS.md) when your backend change affects frontend routes, generated API types, or the websocket/runtime UX contract.

Backend source-of-truth files:

- `pyproject.toml` for dependencies and published CLI entrypoints
- `Makefile` for validation and release targets
- `src/fleet_rlm/api/main.py` for route mounting and SPA asset resolution
- `src/fleet_rlm/cli/fleet_cli.py` and `src/fleet_rlm/cli/main.py` for CLI behavior
- `src/fleet_rlm/cli/runners.py` for top-level runtime builders

## Agent Priorities

- Preserve the backend/frontend runtime contract before optimizing internals.
- Treat websocket event shape and session lifecycle as shared product surface, not backend-only implementation detail.
- Keep CLI docs and examples aligned with the actual Typer commands.
- Update AGENTS/docs when you discover a stable backend workflow or change runtime behavior.
- Prefer the smallest validation lane that covers the change, then escalate to `make quality-gate` for shared-contract work.

## Package Map

Active top-level areas under `src/fleet_rlm/`:

- `_scaffold/`: bundled skills, agents, teams, and hooks exposed by `fleet-rlm init`
- `api/`: FastAPI app, auth, routers, schemas, execution helpers, and server utilities
- `cli/`: Typer CLI entrypoints, commands, and runtime builder constructors
- `conf/`: Hydra config defaults
- `core/`: chat/runtime logic, DSPy modules, execution drivers, tools, and Modal sandbox runtime
- `features/`: analytics, chunking, document ingestion, logs, scaffold assets, and terminal UX
- `infrastructure/`: config, database, MCP server, Daytona provider, and Modal provider integrations
- `ui/`: packaged built frontend assets for installed distributions
- `utils/`: shared helpers including regex, Modal, and scaffold utilities

## Backend Contract

Published CLI entrypoints from `pyproject.toml`:

- `fleet`: lightweight terminal entrypoint, with `fleet web` delegating into `serve-api`
- `fleet-rlm`: full Typer CLI
- `rlm-modal`: alias of `fleet-rlm`

Preserve these command surfaces:

- `fleet web`
- `fleet-rlm chat`
- `fleet-rlm serve-api`
- `fleet-rlm serve-mcp`
- `fleet-rlm init`
- `fleet-rlm daytona-smoke`
- `fleet-rlm daytona-rlm`

Important CLI/runtime nuances:

- `fleet web` is not a separate backend implementation. It rewrites into `fleet-rlm serve-api --host 0.0.0.0 --port 8000`.
- `daytona-rlm` still exposes `--max-depth` only as a deprecated compatibility flag.
- Daytona websocket requests do not accept request-side `max_depth`; schema enforcement happens on the server side.

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

Runtime-mode boundaries:

- `modal_chat`: builds `RLMReActChatAgent`
- `daytona_pilot`: builds `DaytonaWorkbenchChatAgent`
- `execution_mode` is Modal-only
- Daytona request controls are `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
- `src/fleet_rlm/api/routers/ws/chat_runtime.py` is the runtime-mode switch point
- Runtime volume routes accept an optional `provider=modal|daytona` override so the Volumes page can browse either backend without mutating the global sandbox setting

Auth, persistence, and analytics constraints:

- Supported auth modes are `dev` and `entra`
- `AUTH_MODE=entra` requires `AUTH_REQUIRED=true`, `DATABASE_REQUIRED=true`, `ENTRA_JWKS_URL`, `ENTRA_AUDIENCE`, and an issuer template containing `{tenantid}`
- Tenant admission depends on repository-backed tenant and user lookup, not only token validation
- `PATCH /api/v1/runtime/settings` is blocked unless `APP_ENV=local`
- PostHog and MLflow are both active runtime codepaths and should not be documented as optional no-ops when configured

## Agent Operating Rules

- Keep host-side Modal adapters in `core/` separate from sandbox-side protocol helpers.
- Keep DSPy signatures and runtime modules centralized under `core/agent/signatures.py` and `core/models/rlm_runtime_modules.py`.
- Keep `src/fleet_rlm/api/routers/runtime.py` thin. Route orchestration for runtime settings, diagnostics, status assembly, and volume browsing now lives under `src/fleet_rlm/api/runtime_services/*`.
- Keep websocket event shaping and session lifecycle inside `api/routers/ws/*`; treat that layer as a contract with the frontend workspace.
- Treat `/api/v1/ws/chat` as the conversational stream and `/api/v1/ws/execution` as the canonical execution/workbench stream. Do not reintroduce Daytona-only workbench hydration through chat-final payload scraping.
- Daytona host-loop chat should emit live canonical `trajectory_step`, `reasoning_step`, `status`, `warning`, `tool_call`, and `tool_result` events during execution; do not defer the entire trace to the terminal `final` payload.
- Keep runtime streaming websocket-first. FastAPI `StreamingResponse` / SSE may be used only for narrow read-only HTTP flows with a clear product win; they are not the default replacement for workspace/chat/execution websockets.
- Treat the official Daytona docs as the normative baseline for backend integration:
  - Python SDK: [docs](https://www.daytona.io/docs/en/python-sdk/)
  - Volumes: [docs](https://www.daytona.io/docs/en/volumes/)
  - Recursive Language Models / DSPy: [docs](https://www.daytona.io/docs/en/guides/recursive-language-models)
- For repo-specific Daytona architecture decisions and intentional deviations, see
  [docs/reference/daytona-runtime-architecture.md](../../docs/reference/daytona-runtime-architecture.md).
- Daytona remains experimental and intentionally uses a custom recursive host-loop runner with `dspy.Predict`-backed grounding/decomposition/synthesis modules. Do not collapse it into generic `dspy.RLM` language.
- `DAYTONA_TARGET` is Daytona SDK routing/config only. Do not treat it as a workspace id, sandbox id, or volume name.
- The workspace Daytona persistent volume is derived from the authenticated workspace/tenant claim, created/read through `client.volume.get(..., create=True)`, and mounted into Daytona sandboxes through `VolumeMount`.
- Root and recursive Daytona child runs should share the same workspace-scoped persistent volume when one is configured, while still using distinct Daytona sandbox sessions per child run.
- Keep Daytona chat/session normalization helpers in `infrastructure/providers/daytona/chat_state.py` so `chat_agent.py` stays focused on session lifecycle and stream orchestration.
- Reuse `src/fleet_rlm/utils/regex.py` for regex helpers instead of creating new local variants.

## Canonical Commands

Backend setup and runtime:

- `uv sync --all-extras --dev`
- `uv run fleet web`
- `uv run fleet-rlm serve-api --port 8000`
- `uv run fleet-rlm serve-mcp --transport stdio`

Daytona workflows:

- `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]`
- `uv run fleet-rlm daytona-rlm [--repo <url>] [--context-path <path> ...] --task <text> [--batch-concurrency N]`

## Validation by Change Type

Fast backend confidence:

- `make test-fast`

Shared-contract or release-sensitive backend work:

- `make quality-gate`

Focused backend/runtime coverage:

- `uv run pytest -q tests/ui/server/test_api_contract_routes.py tests/ui/server/test_router_runtime.py tests/ui/ws/test_chat_stream.py tests/unit/test_ws_chat_helpers.py`

Daytona-focused backend coverage:

- `uv run pytest -q tests/unit/test_daytona_rlm_config.py tests/unit/test_daytona_rlm_smoke.py tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_cli.py`

Keep command examples aligned with `Makefile`, `pyproject.toml`, and the live router/schema contract.
