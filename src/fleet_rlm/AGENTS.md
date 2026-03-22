# Backend Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents modifying the backend under `src/fleet_rlm/`.
Read the root [AGENTS.md](../../AGENTS.md) first for shared repo rules.
Only consult [src/frontend/AGENTS.md](../frontend/AGENTS.md) when your backend change affects frontend routes, generated API types, or the websocket/runtime UX contract.

Backend source-of-truth files:

- `pyproject.toml` for dependencies and published CLI entrypoints
- `Makefile` for validation and release targets
- `src/fleet_rlm/api/main.py` for app factory, lifespan orchestration, route mounting, and SPA asset resolution
- `src/fleet_rlm/api/bootstrap.py` for runtime bootstrap, non-blocking optional startup, LM loading, analytics startup, and persistence initialization
- `src/fleet_rlm/cli/fleet_cli.py` and `src/fleet_rlm/cli/main.py` for CLI behavior
- `src/fleet_rlm/cli/runtime_factory.py` for `ServerRuntimeConfig` / `MCPRuntimeConfig` assembly
- `src/fleet_rlm/cli/runners.py` for top-level runner helpers

## Agent Priorities

- Preserve the backend/frontend runtime contract before optimizing internals.
- Treat websocket event shape and session lifecycle as shared product surface, not backend-only implementation detail.
- Keep CLI docs and examples aligned with the actual Typer commands.
- Update AGENTS/docs when you discover a stable backend workflow or change runtime behavior.
- Prefer the smallest validation lane that covers the change, then escalate to `make quality-gate` for shared-contract work.

## Package Map

Active top-level areas under `src/fleet_rlm/`:

- `api/`: FastAPI app, auth, routers, schemas, execution helpers, and server utilities
- `cli/`: Typer CLI entrypoints, commands, and runtime builder constructors
- `runtime/`: chat/runtime logic, DSPy modules, execution drivers, content processing, tools, and runtime models
- `integrations/`: config, database, MCP server, observability, Daytona provider, and Modal provider integrations
- `scaffold/`: bundled skills, agents, teams, and hooks exposed by `fleet-rlm init`
- `ui/`: packaged built frontend assets for installed distributions
- `utils/`: shared helpers including regex and Modal utilities

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

Important CLI/runtime nuances:

- `fleet web` is not a separate backend implementation. It rewrites into `fleet-rlm serve-api --host 0.0.0.0 --port 8000`.
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
- `daytona_pilot`: uses the same `RLMReActChatAgent` backbone, configured with a Daytona-backed interpreter (`DaytonaWorkbenchChatAgent` is the focused Daytona-specific agent layer over that shared runtime)
- `execution_mode` is Modal-only
- Daytona request controls are `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
- `src/fleet_rlm/api/routers/ws/session.py` normalizes websocket runtime state and session switching
- Runtime volume routes accept an optional `provider=modal|daytona` override so the Volumes page can browse either backend without mutating the global sandbox setting

Auth, persistence, and analytics constraints:

- Supported auth modes are `dev` and `entra`
- `AUTH_MODE=entra` requires `AUTH_REQUIRED=true`, `DATABASE_REQUIRED=true`, `ENTRA_JWKS_URL`, `ENTRA_AUDIENCE`, and an issuer template containing `{tenantid}`
- Tenant admission depends on repository-backed tenant and user lookup, not only token validation
- `PATCH /api/v1/runtime/settings` is blocked unless `APP_ENV=local`
- PostHog and MLflow are both active runtime codepaths and should not be documented as optional no-ops when configured
- `/ready` now reports critical server readiness only. Planner/delegate LM warmup, PostHog startup, and MLflow startup are optional background tasks; inspect runtime status/diagnostics for those service states instead of treating them as boot blockers.

## Agent Operating Rules

- Keep transport logic in `api/` only. Business/runtime behavior belongs in `runtime/` or `integrations/`.
- Keep DSPy signatures and runtime modules centralized under `runtime/agent/signatures.py` and `runtime/models/rlm_runtime_modules.py`.
- Keep shared chat/runtime behavior under `runtime/agent/*` and `runtime/execution/*`; avoid reintroducing compatibility wrappers at the package root.
- Keep content-oriented helpers under `runtime/content/*` instead of splitting chunking, document ingestion, and logs across a generic `features/` namespace.
- Keep Modal sandbox driver assets and output utilities under `runtime/execution/*`:
  - `runtime/execution/sandbox_assets.py` for bundled sandbox helper functions
  - `runtime/execution/output_utils.py` for stdout/stderr redaction and summarization
- Keep Modal volume persistence and browsing in `runtime/tools/modal_volumes.py`; keep Daytona volume browsing in `integrations/providers/daytona/volumes.py`.
- Keep the shared sandbox tool surface consolidated in `runtime/tools/sandbox.py`, including recursive RLM delegation, cached-runtime analysis tools, memory-intelligence tools, and persistent memory helpers.
- Keep `src/fleet_rlm/api/routers/runtime.py` thin. Route orchestration for runtime settings, diagnostics/status assembly, and volume browsing now lives under:
  - `src/fleet_rlm/api/runtime_services/settings.py`
  - `src/fleet_rlm/api/runtime_services/diagnostics.py`
  - `src/fleet_rlm/api/runtime_services/volumes.py`
- Keep `src/fleet_rlm/api/main.py` limited to app factory, lifespan orchestration, route registration, and SPA mounting. Runtime startup and teardown belong in `src/fleet_rlm/api/bootstrap.py`.
- Keep runtime config imports lightweight. Config-only modules and package roots must not import DSPy, provider SDKs, MLflow runtime helpers, or PostHog callbacks as import-time side effects.
- Keep DSPy boundaries explicit:
  - semantic task contracts in `runtime/agent/signatures.py`
  - orchestration programs/modules in `runtime/agent/*` and `runtime/models/rlm_runtime_modules.py`
  - typed tool adapters in `runtime/tools/*`
- Keep the shared chat/runtime ownership split explicit inside `runtime/agent/`:
  - `runtime/agent/chat_agent.py` for the public `RLMReActChatAgent` facade, lifecycle, ReAct module rebuilds, and execution-mode switching
  - `runtime/agent/chat_turns.py` for per-turn delegation state, turn metrics, prediction normalization, and chat-turn result shaping
  - `runtime/agent/recursive_runtime.py` for the canonical recursive child-`dspy.RLM` runtime
- DSPy tool callables used by ReAct-style flows should stay as typed Python functions with clear docstrings. Provider-specific details belong behind the tool adapter boundary, not inside transport code.
- Keep websocket event shaping and session lifecycle inside the websocket
  transport package, with focused ownership per module:
  - `api/routers/ws/endpoint.py` for route entrypoints and connection wiring
  - `api/routers/ws/stream.py` for the live chat loop and stream emission
  - `api/routers/ws/commands.py` and `api/routers/ws/hitl.py` for command dispatch
  - `api/routers/ws/lifecycle.py`, `turn_setup.py`, and `turn_lifecycle.py` for run/turn lifecycle state
  - `api/routers/ws/runtime.py` and `session.py` for runtime prep and session switching
  - `api/routers/ws/messages.py` for payload parsing and session identity resolution
  - `api/routers/ws/persistence.py`, `manifest.py`, and `artifacts.py` for durable state and manifest updates
  - `api/routers/ws/errors.py`, `failures.py`, `loop_exit.py`, `task_control.py`, `terminal.py`, and `completion.py` for failure handling, cancellation, terminal ordering, and execution summaries
  - `api/routers/ws/types.py` and `execution_support.py` for typed helpers and shared execution-event plumbing
- Treat `/api/v1/ws/chat` as the conversational stream and `/api/v1/ws/execution` as the canonical execution/workbench stream. Do not reintroduce Daytona-only workbench hydration through chat-final payload scraping.
- Daytona-backed chat should emit live canonical `trajectory_step`, `reasoning_step`, `status`, `warning`, `tool_call`, and `tool_result` events during execution through the shared ReAct/RLM flow plus interpreter callbacks; do not defer the entire trace to the terminal `final` payload.
- Keep runtime streaming websocket-first. FastAPI `StreamingResponse` / SSE may be used only for narrow read-only HTTP flows with a clear product win; they are not the default replacement for workspace/chat/execution websockets.
- Treat the official Daytona docs as the normative baseline for backend integration:
  - Python SDK: [docs](https://www.daytona.io/docs/en/python-sdk/)
  - Async SDK: [docs](https://www.daytona.io/docs/en/python-sdk/async/async-daytona/)
  - Async Sandbox: [docs](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/)
  - Async File System: [docs](https://www.daytona.io/docs/en/python-sdk/async/async-file-system/)
  - Async Volume: [docs](https://www.daytona.io/docs/en/python-sdk/async/async-volume/)
  - Async Code Interpreter: [docs](https://www.daytona.io/docs/en/python-sdk/async/async-code-interpreter/)
  - Log Streaming: [docs](https://www.daytona.io/docs/en/log-streaming/)
  - Volumes: [docs](https://www.daytona.io/docs/en/volumes/)
  - Recursive Language Models / DSPy: [docs](https://www.daytona.io/docs/en/guides/recursive-language-models)
- For repo-specific Daytona architecture decisions and intentional deviations, see
  [docs/reference/daytona-runtime-architecture.md](../../docs/reference/daytona-runtime-architecture.md).
- Daytona is now aligned to the shared ReAct + `dspy.RLM` runtime architecture. Keep Daytona-specific behavior in `integrations/providers/daytona/*`, not in a parallel chat/runtime orchestrator.
- `spawn_delegate_sub_agent_async` remains the one true recursive child-RLM path for both Modal and Daytona. `llm_query` stays semantic-only; `rlm_query` and `rlm_query_batched` remain the true child-RLM entrypoints.
- `DAYTONA_TARGET` is Daytona SDK routing/config only. Do not treat it as a workspace id, sandbox id, or volume name.
- The workspace Daytona persistent volume is derived from the authenticated workspace/tenant claim, created/read through `client.volume.get(..., create=True)`, and mounted into Daytona sandboxes through `VolumeMount`.
- Keep Daytona aligned to the official SDK surface with direct `from daytona import ...` imports in the owning modules. Do not reintroduce a local Daytona SDK façade.
- Keep async websocket/session-switch paths on the async helpers (`agent.areset()`, `agent.aimport_session_state()`, `interpreter.aconfigure_workspace()`, and `interpreter.aexecute()`), but the current provider implementation is sync-first internally and uses `asyncio.to_thread(...)` compatibility wrappers rather than an `AsyncDaytona`-first runtime.
- Root and recursive Daytona child runs should share the same workspace-scoped persistent volume when one is configured, while still using distinct Daytona sandbox sessions per child run.
- Daytona interpreter execution is now implemented on top of `sandbox.code_interpreter.run_code()`. The repo keeps only a minimal bridge for host callbacks (`llm_query`, `llm_query_batched`, custom tools) and `SUBMIT(...)` final-artifact capture.
- Keep canonical Daytona internals under `integrations/providers/daytona/` with the provider root modules as the real implementation surface:
  - `runtime.py`
  - `interpreter.py`
  - `bridge.py`
  - `types_budget.py`
  - `types_context.py`
  - `types_recursive.py`
  - `types_result.py`
  - `types_serialization.py`
  - `volumes.py`
- Persistent Daytona memory has two layers:
  - volatile code-interpreter context state inside the live sandbox-side Python process
  - durable workspace state on the mounted Daytona volume at `/home/daytona/memory`
- Keep Daytona chat/session normalization helpers in `integrations/providers/daytona/state.py` so `agent.py` stays a focused Daytona-specific agent/session adapter over the shared runtime.
- Keep terminal session actions in `cli/terminal/session_actions.py` and transcript/rendering helpers in `cli/terminal/session_view.py`.
- Keep MLflow runtime bootstrap, callback registration, and request-context helpers in `integrations/observability/mlflow_runtime.py`; keep trace lookup, feedback logging, and dataset/export helpers in `integrations/observability/mlflow_traces.py`.
- Reuse `src/fleet_rlm/utils/regex.py` for regex helpers instead of creating new local variants.

## Canonical Commands

Backend setup and runtime:

- `uv sync --all-extras --dev`
- `uv run fleet web`
- `uv run fleet-rlm serve-api --port 8000`
- `uv run fleet-rlm serve-mcp --transport stdio`

Daytona workflows:

- `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]`

## Validation by Change Type

Fast backend confidence:

- `make test-fast`

Shared-contract or release-sensitive backend work:

- `make quality-gate`

Focused backend/runtime coverage:

- `uv run pytest -q tests/ui/server/test_api_contract_routes.py tests/ui/server/test_router_runtime.py tests/ui/ws/test_chat_stream.py tests/unit/test_ws_chat_helpers.py`

Daytona-focused backend coverage:

- `uv run pytest -q tests/unit/test_daytona_rlm_config.py tests/unit/test_daytona_rlm_smoke.py tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_daytona_workbench_chat_agent.py`

Keep command examples aligned with `Makefile`, `pyproject.toml`, and the live router/schema contract.
