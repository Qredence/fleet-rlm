# Backend Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents modifying the backend under `src/fleet_rlm/`.
Read the root [AGENTS.md](../../AGENTS.md) first for shared repo rules.
Consult [src/frontend/AGENTS.md](../frontend/AGENTS.md) only when backend changes affect frontend routes, generated API types, websocket payloads, runtime UX, or shared contract metadata.

## Backend Quickstart

Before editing backend code:

- Read `pyproject.toml` and `Makefile` for canonical commands and package surfaces.
- Inspect the owning package (`api`, `cli`, `runtime`, `integrations`, or `utils`) before adding files.
- Preserve the backend/frontend runtime contract before optimizing internals.
- Keep route/transport modules thin and move business logic into owning runtime or integration modules.
- Avoid import-time side effects in config-only and package-root modules.

Backend source-of-truth files:

- `pyproject.toml` for dependencies and published CLI entrypoints
- `Makefile` for validation and release targets
- `src/fleet_rlm/api/main.py` for app factory, lifespan orchestration, route mounting, and SPA asset resolution
- `src/fleet_rlm/api/bootstrap.py` for runtime bootstrap, optional startup, LM loading, analytics startup, and persistence initialization
- `src/fleet_rlm/cli/fleet_cli.py` and `src/fleet_rlm/cli/main.py` for CLI behavior
- `src/fleet_rlm/runtime/factory.py` for canonical runtime construction
- `src/fleet_rlm/cli/runners.py` for top-level runner helpers

Artifacts and areas to treat carefully:

- The bundled UI dist output is generated from the frontend build, not handwritten backend source, and may be absent in a fresh source checkout until packaging/build steps run
- `migrations/` and database-facing schema changes should stay aligned with persistence behavior
- `openapi.yaml` is generated from backend route/schema metadata and should be regenerated, not manually patched

## Agent Priorities

- Preserve the backend/frontend runtime contract before refactoring internals.
- Treat websocket event shape and session lifecycle as shared product surface, not backend-only implementation detail.
- Keep CLI docs and examples aligned with the actual Typer and argparse entrypoints.
- Always run `make format`, `make lint`, and `make typecheck` before commit or PR for backend or shared Python changes.
- Prefer the smallest validation lane that covers the change, then escalate to `make quality-gate` for shared-contract work.
- When route/schema metadata changes, regenerate `openapi.yaml` with `uv run python scripts/openapi_tools.py generate` before frontend sync checks.

## Package Map

Active top-level areas under `src/fleet_rlm/`:

- `api/`: thin FastAPI app, auth, routers, schemas, websocket lifecycle, event shaping, and server utilities (also hosts terminal flow, HITL checkpointing, and hosted policy orchestration)
- `cli/`: Typer/argparse entrypoints, commands, and runtime builder constructors
- `runtime/`: shared recursive chat/runtime logic, DSPy modules, execution drivers, content processing, tools, and runtime modules
  - `runtime/modules/` hosts native `dspy.RLM` factories, module registry definitions, the `RecursiveWorkspaceModule` thin orchestrator (`workspace.py`), and its per-phase `dspy.Module`s (`workspace_phases.py`); large inputs cross into the sandbox as `dspy.SandboxSerializable` models from `runtime/sandbox_types.py` (no variable-mode wrapper)
  - `runtime/tools/rlm_delegate.py` owns `delegate_to_rlm` / `delegate_to_rlm_batched` plus host-side trajectory persistence into `external_traces`
- `integrations/`: config, database, observability, and external-system integrations
  - `integrations/daytona/isolation.py` exposes host-mediated `store_evidence` / `fetch_evidence` / `list_evidence` to sandbox code via `bridge_callbacks.py`; `DATABASE_URL` is never exposed to the sandbox
  - `integrations/database/repository_chat.py::store_rlm_trace` persists RLM child trajectories to `external_traces`
- `ui/`: packaged built frontend assets for installed distributions
- `utils/`: shared helpers

## Backend Contract

Published CLI entrypoints from `pyproject.toml`:

- `fleet`
- `fleet-rlm`

Preserve these command surfaces:

- `fleet web`
- `fleet-rlm chat`
- `fleet-rlm serve-api`
- `fleet-rlm daytona-smoke`

Important CLI/runtime nuances:

- `fleet web` is a thin entrypoint that delegates into `fleet-rlm serve-api --host 0.0.0.0 --port 8000`
- `fleet-rlm chat` owns the interactive terminal TUI; preserve the prompt_toolkit boxed input,
  scrollable transcript, and visible thinking/connection states when editing `chat.py`
- Daytona websocket requests do not accept request-side `max_depth`; schema enforcement happens server-side
- `/ready` reports critical server readiness only; optional LM and observability warmup status belongs in runtime diagnostics/status

Canonical HTTP and websocket surfaces:

- `/health`
- `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `GET /api/v1/sessions` — paginated session history with search/status filters
- `GET /api/v1/sessions/{id}` — session detail with turn count
- `GET /api/v1/sessions/{id}/turns` — paginated turn transcript
- `GET /api/v1/sessions/{id}/stats` — aggregated usage stats
- `DELETE /api/v1/sessions/{id}` — archive (soft-delete) session
- `POST /api/v1/sessions/{id}/restore` — unarchive a session
- `POST /api/v1/sessions/{id}/export` — export session as a GEPA dataset
- `POST /api/v1/sessions/{id}/trace-export` — export raw MLflow traces plus a distilled GEPA bundle
- Session trace lookup surfaces must accept the durable chat session id as the
  primary selector and may resolve runtime `external_session_id` aliases for
  MLflow-backed traces
- `GET/PATCH /api/v1/runtime/settings`
- `POST /api/v1/runtime/tests/daytona`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`
- `GET /api/v1/runtime/volume/tree`
- `GET /api/v1/runtime/volume/file`
- `GET /api/v1/sandboxes` — list active sandboxes
- `GET /api/v1/sandboxes/{id}` — sandbox detail
- `DELETE /api/v1/sandboxes/{id}` — delete sandbox
- `POST /api/v1/sandboxes/{id}/archive` — archive sandbox
- `GET /api/v1/optimization/status`
- `POST /api/v1/optimization/run`
- `GET /api/v1/optimization/modules`
- `POST /api/v1/optimization/runs`
- `GET /api/v1/optimization/runs`
- `GET /api/v1/optimization/runs/{run_id}`
- `GET /api/v1/optimization/runs/{run_id}/results`
- `GET /api/v1/optimization/runs/compare`
- `POST /api/v1/optimization/datasets`
- `GET /api/v1/optimization/datasets`
- `GET /api/v1/optimization/datasets/{dataset_id}`
- `POST /api/v1/traces/feedback`
- `/api/v1/ws/execution`
- `/api/v1/ws/execution/events`
- Optional `/scalar` docs when `scalar_fastapi` is installed

Runtime-mode boundaries:

- The public backend/frontend runtime contract is Daytona-only.
- The canonical chat runtime is the Daytona-backed recursive DSPy ReAct + `dspy.RLM` workbench agent.
- Request-side `runtime_mode` selection is not part of the public websocket contract.
- `execution_mode` remains a per-turn execution hint for the Daytona-backed runtime
- Daytona request controls are `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
- Runtime volume routes are Daytona-only on the public surface

Auth, persistence, and observability constraints:

- Supported auth modes are `dev`, `entra`, and `neon`
- `AUTH_MODE=entra` and `AUTH_MODE=neon` require repository-backed tenant admission in addition to token validation
- Neon Auth tokens are EdDSA-signed; backend JWT decoding must explicitly allow `EdDSA`
  and continue validating issuer, audience, timestamps, and repository admission.
- Neon Auth browser WebSockets must use the short-lived `/api/v1/auth/ws-ticket` exchange; do not put raw Neon JWTs in WebSocket query strings
- `DATABASE_URL` is the pooled runtime connection; `DATABASE_ADMIN_URL` is the direct connection for Alembic and admin/debug tasks
- Postgres Row-Level Security depends on transaction-local `app.tenant_id`,
  `app.user_id`, and `app.workspace_id` context set through the repository boundary.
  Do not bypass `FleetRepository` or route browser product data directly through Neon Data API.
- `PATCH /api/v1/runtime/settings` is blocked unless `APP_ENV=local`
- Hosted `AUTH_MODE=neon` LLM provider profiles are per-user BYOK data. Profile routes must require
  repository-admitted identity, use tenant/user-scoped Postgres access, and never mirror user secrets into `.env`.
- `POST /api/v1/runtime/llm-profiles/import-env` is local-only; do not import server environment secrets into hosted user profiles.
- Hosted BYOK profile encryption requires `FLEET_SECRET_ENCRYPTION_KEY`.
- PostHog and MLflow are live codepaths when configured and should not be treated as no-ops
- In local development, MLflow may auto-start when configured for localhost unless `MLFLOW_AUTO_START=false`

## Agent Operating Rules

Layering rules:

- Keep hosted policy, terminal flow, and HITL checkpointing in `api/`; the outer-host responsibilities now live alongside transport logic
- Keep transport logic in `api/` only
- Keep recursive business/runtime behavior in `runtime/` or `integrations/daytona/`
- Keep runtime config imports lightweight; config/package-root modules must not import DSPy, provider SDKs, MLflow runtime helpers, or PostHog callbacks as import-time side effects
- Reuse existing helpers before introducing new compatibility wrappers

Runtime ownership:

- Keep DSPy signatures in `runtime/agent/signatures.py`
- Keep runtime module construction/registration in `runtime/modules/factory.py`, `runtime/modules/registry.py`, or the `fleet_rlm.runtime.modules` package exports
- Keep the main cognition loop in `runtime/agent/agent.py` (`FleetAgent`, a thin
  `dspy.ReAct` subclass), `runtime/modules/escalating.py` (`EscalatingFleetModule`, the
  default CoT→ReAct→RLM router), and `runtime/agent/runtime.py` (AgentRuntime)
- Keep the public Daytona interpreter facade in `integrations/daytona/interpreter.py`; durable workspace/session behavior lives in `workspace_manager.py`, code execution and bridge state live in `sandbox_executor.py`, and recursive child policy/delegation lives in `isolation.py`.
- Keep Daytona collaborator boundaries typed with small internal Protocols. Use Pydantic v2 for validated configuration/state boundary models such as `WorkspaceConfig`, but keep hot execution-path payloads and bridge result carriers as dataclasses/functions.
- Keep runtime orchestration and shared chat/runtime behavior under `runtime/agent/*` and `runtime/execution/*`
- Keep content-oriented helpers under `runtime/content/*`
- Keep DSPy evaluation and optimization helpers under `quality/*`
- Keep shared evaluation infrastructure in `quality/datasets.py`, `quality/scoring_helpers.py`, `quality/artifacts.py`, `quality/module_registry.py`, and `quality/optimization_runner.py`
- Keep per-module optimization entrypoints in `quality/optimize_*.py`; each must register a `ModuleOptimizationSpec` in the module registry
- The module registry (`module_registry.py`) is the single source of truth for optimizable modules, consumed by CLI, API, and frontend. **Note:** `longcot-reasoner` is currently registered via `fleet_rlm.quality.optimize_longcot`; add new `quality/optimize_*.py` entrypoints to `_MODULE_ENTRYPOINTS` as more modules become optimizable.
- Optimization is GEPA-only via `quality/optimization_runner.run_module_optimization` and runs offline only — never in the live request path
- Keep grouped tool helpers under root `runtime/tools/*`
- Keep DSPy-native MCP tool discovery in `runtime/tools/mcp_tools.py`. It is opt-in:
  servers are configured via the `FLEET_RLM_MCP_SERVERS` env var (JSON array) and
  attached through `AgentRuntime.attach_mcp_tools(...)`; nothing connects unless set.
  Do not add import-time MCP/`dspy` side effects (lazy-import inside `connect()`).

API ownership:

- Keep `src/fleet_rlm/api/main.py` limited to app factory, lifespan orchestration, route registration, and SPA mounting
- Keep runtime startup/shutdown in `src/fleet_rlm/api/bootstrap.py`
- Keep `src/fleet_rlm/api/routers/runtime.py` thin; runtime service orchestration belongs in `src/fleet_rlm/api/runtime_services/*`
- Keep websocket runtime preparation in `src/fleet_rlm/api/runtime_services/chat_runtime.py`
- Keep websocket run/session persistence orchestration in `src/fleet_rlm/api/runtime_services/chat_persistence.py`
- Keep websocket event shaping and session lifecycle inside `src/fleet_rlm/api/routers/ws/*`

Websocket/runtime contract rules:

- Treat `/api/v1/ws/execution` as the canonical conversational websocket stream
- Canonical runtime streaming events are `RuntimeEvent` in `runtime/events.py`; project them with `api/events/project_chat.py` (not legacy `StreamEvent` DTOs). `runtime/schemas.py` only exports shared `TraceMode`.
- Treat `/api/v1/ws/execution/events` as the dedicated passive execution/workbench event stream
- Keep `/api/v1/ws/execution` execution-only:
  - accept auth plus websocket frames
  - reject query `session_id`
  - use message-level `session_id` only to allocate, restore, or continue chat sessions
  - emit an early `status` event when initial Daytona startup is slow so the client does not hit first-frame timeout before the real reply or startup error
- Keep `/api/v1/ws/execution/events` subscription-only:
  - require query `session_id`
  - do not route message, cancel, or command frames through this socket
  - emit only `execution_started`, `execution_step`, and `execution_completed`
- Keep websocket workspace/user identity auth-derived on both routes; reject client-supplied `workspace_id` and `user_id`
- Treat `execution_completed.summary` as the canonical workbench/sidepanel hydration payload
- Keep session trace and graph lookup tolerant of missing MLflow rows: the
  frontend workspace sidepanel should still be able to render from live
  transcript and artifact summaries when trace storage is unavailable.
- Keep interpreter-originated REPL execution steps wired through `execution_event_callback` and preserve any previously installed callback when bridging hooks
- Do not reintroduce Daytona-only workbench hydration through chat-final payload scraping
- Daytona-backed chat should emit live canonical `trajectory_step`, `reasoning_step`, `status`, `warning`, `tool_call`, and `tool_result` events during execution
- When Daytona falls back after a controlled failure, preserve the answer but mark the turn as degraded in final payloads and MLflow metadata
- Prefer websocket-first streaming; do not replace workspace/chat streams with SSE without a clear product need

Daytona-specific boundaries:

- Keep Daytona-specific behavior under `integrations/daytona/*`
- Prefer Daytona SDK services directly for sandbox lifecycle, git, filesystem, preview/LSP, and code-interpreter operations; Fleet wrappers should exist only for product policy, diagnostics, session state, ownership labels, volume layout, context staging, manifests, and the RLM host-callback bridge.
- Keep recursive child sandbox policy, concrete child delegation hooks, host-mediated evidence persistence, and context staging in `integrations/daytona/isolation.py` until one of those responsibilities becomes large enough to justify a real split.
- Keep Daytona RLM bridge callback dispatch in `integrations/daytona/bridge_callbacks.py`; bridge-owned callback names must continue to route through Fleet interpreter methods before custom tools.
- Keep Daytona client construction, config resolution, and SDK error classification in `integrations/daytona/config.py`
- Keep sandbox spec building, payload/session normalization, workspace config models, and diagnostic result models in `integrations/daytona/models.py`.
- Keep volume readiness, mount context managers, inventory, browsing, snapshot management, and low-level SDK operations in `integrations/daytona/sdk_ops.py`; accept SDK enum-style states such as `VolumeState.READY` in addition to raw tokens like `ready`
- Keep workspace path helpers, git ref resolution, repo checkout, and workspace session orchestration in `integrations/daytona/workspace_runtime.py`; use SDK `git.clone`, `git.status`, `git.pull`, `git.branches`, and `git.checkout_branch` where they preserve behavior, and allow named `sandbox.process.exec` fallbacks only for remote URL mismatch, non-git workspace replacement, exact forced remote reset, and detached commit checkout semantics not exposed by the SDK.
- Keep `DaytonaSandboxSession` dataclass and admin code-execution helpers in `integrations/daytona/session_runtime.py`; public `a*` methods must stay awaitable while sync compatibility methods remain available for notebooks and tests.
- Keep resume/fork diagnostics in `integrations/daytona/sdk_ops.py`; do not add new thin lifecycle wrappers for SDK methods that can be called directly from `DaytonaSandboxSession` or `DaytonaSandboxRuntime`.
- Keep structured diagnostic errors and phase-to-category mapping in `integrations/daytona/diagnostics.py`
- Keep the async/sync bridge helpers in `integrations/daytona/async_compat.py`; sync callers should use `asyncio.run` when no loop is active and a short-lived background thread bridge when one is already running
- Keep async Neon/Postgres persistence under `integrations/database/*` with the concrete `FleetRepository` as the canonical repo boundary
- Keep the lightweight SQLite sidecar for local sessions/history/optimization in `integrations/local_store.py`
- Treat `DaytonaSandboxRuntime` and `DaytonaSandboxSession` as the canonical internal async contract
- Keep Daytona SDK lifecycle helpers in `integrations/daytona/sdk_ops.py` and runtime factory in `integrations/daytona/runtime.py`
- When Daytona volume readiness times out or fails, include both the raw SDK state and the normalized canonical state in diagnostics where they differ
- Keep the durable mounted-volume roots aligned to `/home/daytona/memory/{memory,artifacts,buffers,meta}`
- Keep recursive RLM child creation centralized through `integrations/daytona/isolation.py::build_delegate_child`, re-exported by `integrations/daytona/interpreter.py`; both host `delegate_to_rlm()` and sandbox `sub_rlm()` / `sub_rlm_batched()` must use it.
- Default recursive isolation is `RLM_CHILD_ISOLATION_MODE=auto`: fork no-volume parents, use clean child sandboxes with `meta/rlm-children/...` volume subpaths for volume-mounted parents, and delete child sandboxes after every recursive task. `context` mode is a local/debug opt-out only.
- Dispatch bridged `llm_query*` and `sub_rlm*` callbacks through Daytona interpreter methods so recursion depth and `rlm_max_llm_calls` remain shared across recursive children.
- For local host-checkout codebase questions without `repo_url`, keep `delegate_to_rlm()` snapshot staging bounded and explicit: write relevant local evidence to the isolated child sandbox under `artifacts/rlm-inputs/local_workspace_snapshot.txt`; do not silently share the parent filesystem.
- Restore session manifests as replacement state: default core memory plus persisted memory, loaded document paths, conversation history, and Daytona interpreter state. Do not merge stale session-local memory into a new identity.
- Do not auto-promote child sandbox files or artifacts into the parent; recursive child results return through the RLM answer.
- Treat the live Daytona workspace as transient repo/execution state with no implicit workspace-to-volume sync
- Keep `rlm_query` as the shared agent-level recursive entrypoint; `rlm_query_batched` remains Daytona-only
- Daytona idle lifecycle is timer-driven:
  - `auto_stop_interval=30`
  - `auto_archive_interval=60`
  - treat these values as provider minutes, not seconds
- Sandbox concurrency control:
  - Global `asyncio.Semaphore` caps total active sandboxes (root + child RLMs)
  - Configured via `FLEET_MAX_CONCURRENT_SANDBOXES` env var (default: 5, range: 1–50)
  - Acquisition timeout is 60 seconds; exceeding raises `DaytonaDiagnosticError(category="sandbox_concurrency_busy")`
  - Slots are auto-released when `sandbox.delete()` or `sandbox.stop()` is called
  - Pydantic models: `ConcurrencyConfig` (config) and `SandboxUsageStats` (diagnostics) in `integrations/daytona/concurrency.py`
  - `get_current_sandbox_usage()` returns `SandboxUsageStats` for runtime diagnostics

Tooling boundaries:

- The shared backend/frontend product contract is Daytona-only; do not reintroduce Modal provider surfaces.
- Reuse helpers in `src/fleet_rlm/utils/` for regex helpers instead of creating local variants

Common mistakes to avoid:

- Putting business logic into routers or CLI entrypoints
- Adding heavy imports to config/package roots
- Reintroducing parallel Daytona chat/runtime orchestrators outside the shared recursive runtime
- Hand-editing packaged UI build output or generated OpenAPI artifacts
- Treating Volumes or `/ready` semantics differently from the implemented contract

## Phase 7: RLM Recursion and History Management

Phase 7 aligns the `dspy.RLM` path and recursion with the reference implementation, focusing on structured history management, explicit depth tracking, and token-budget-aware compaction.

### P7.1: History as Native REPL Variable

- The RLM turn signatures (`RLMTurnSignature`, `RLMDocumentTurnSignature`, `RLMWorkspaceTurnSignature`) include `history: dspy.History` as an `InputField`
- `EscalatingFleetModule._run_rlm` always passes the `history` object to the RLM module
- The model can inspect full prior conversation turns with code (e.g., `history.messages[-1]`) rather than relying solely on flattened recency snippets

### P7.2: Bounded Redacted Conversation Snapshot to Recursive Children

- `LLMQueryMixin._execute_sub_rlm` builds a bounded, redacted conversation snapshot for child contexts
- `_build_child_history_snapshot()` extracts the last N turns (default: 2) from the parent runtime's history
- Sensitive values (API keys, tokens, passwords) are redacted using pattern-based replacement
- Snapshot size is bounded (default: 2000 chars) and truncated with a marker if exceeded
- Children receive a fresh REPL (per reference) but get explicit conversation continuity

### P7.3: Token-Budget-Aware Compaction

- `AgentRuntime._maybe_refresh_summary()` now compacts history based on estimated token usage
- `_estimate_history_chars()` provides a character-count proxy for token estimation (4 chars/token approximation)
- Compaction triggers when history exceeds the configured threshold (default: 70% of 64K token context window) or the turn interval is reached
- `history_max_turns` remains a hard ceiling for turn-based truncation
- New `compaction_threshold_pct` parameter controls the token-budget threshold

### P7.4: Explicit Depth Tracking and Fallback

- `AgentRuntime._recursion_depth_state()` returns `(depth, max_depth)` from interpreter state
- `RuntimeEventContext` includes `depth` and `max_depth` fields surfaced on runtime events
- `sub_rlm` and `sub_rlm_batched` fall back to `llm_query` and `llm_query_batched` when max recursion depth is reached
- Fallback prevents infinite recursion while preserving answer quality

### P7.5: Benchmark Fast-Paths Removed

- Confirmed no benchmark fast-paths exist in `rlm_delegate.py` (already removed in earlier phases)

## Canonical Commands

Backend setup and runtime:

- `uv sync --all-extras`
- `uv run fleet web`
- `uv run fleet-rlm serve-api --port 8000`
- `uv run python scripts/openapi_tools.py generate`
- `uv run python scripts/openapi_tools.py validate`

Daytona workflow:

- `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]`

## Known Pre-Existing Issues

These issues are documented for awareness. Workers should not attempt fixes unless explicitly tasked.

- **Import-time side effects in runtime packages** — `quality/__init__.py` and `quality/scorers.py` import DSPy or MLflow at the top level. The observability package mitigates this with lazy `__getattr__`; quality packages do not yet.
- **Module registry entrypoint drift** — `quality/module_registry.py` currently seeds `_MODULE_ENTRYPOINTS` with `fleet_rlm.quality.optimize_longcot`, which registers `longcot-reasoner`. Keep `_MODULE_ENTRYPOINTS` aligned with any additional `quality/optimize_*.py` modules so CLI/API metadata stays accurate.
- **Import-time side effects in `cli/runners.py`** — Top-level imports of `dspy`, `DaytonaInterpreter`, and MLflow observability modules. This is mitigated by lazy loading in `cli/__init__.py` but still violates the import-time rule.
- **`daytona/interpreter.py` eagerly imports `dspy`** — Any upstream import of `DaytonaInterpreter` loads DSPy into the process.

## Validation by Change Type

Mandatory baseline for backend or shared Python edits:

- `make format`
- `make lint`
- `make typecheck`

Fast backend confidence:

- `make test-fast` (alias for `make test`)

Focused backend/runtime coverage:

- `uv run pytest -q tests/unit/api/ws/test_transport.py tests/unit/api/ws/test_execution_helpers.py tests/unit/api/ws/test_chat_persistence.py tests/unit/package/test_exports.py tests/unit/api/test_auth.py`

Daytona-focused backend coverage:

- `uv run pytest -q tests/unit/integrations/daytona/test_config.py tests/unit/integrations/daytona/test_smoke.py tests/unit/integrations/daytona/test_runtime.py tests/unit/integrations/daytona/test_interpreter.py tests/unit/runtime/agent/test_runtime.py tests/unit/runtime/agent/test_sub_rlm.py tests/unit/runtime/tools/test_rlm_delegate.py`

Shared-contract or release-sensitive work:

- `make quality-gate`

Keep command examples aligned with `Makefile`, `pyproject.toml`, and the live router/schema contract.
