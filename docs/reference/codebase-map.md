# fleet-rlm Full Code Map and Simplification Audit

This document is the current package-level architecture map for `src/fleet_rlm`.
It replaces the older Wave 7.2 ownership snapshot with a fuller view of how the
backend runtime, support packages, terminal surface, packaged UI, and scaffold
assets fit together today.

Use this alongside:

- [Source Layout](source-layout.md) for a shorter package inventory
- [Frontend ↔ Backend Integration](frontend-backend-integration.md) for the web contract

## Reading Guide

- `runtime-critical`: part of the live execution path for agent turns
- `service-surface`: user or external entrypoint that exposes the runtime
- `support-layer`: shared config, analytics, helpers, or pure utilities
- `developer-tooling`: packaged assets or install-time helpers, not part of live request handling

## Top-Level Package Inventory

### Standalone modules

| Path | Role | Classification |
| --- | --- | --- |
| `src/fleet_rlm/__init__.py` | Lazy public export surface for the package API, version marker, and compatibility-friendly re-exports. | support-layer |
| `src/fleet_rlm/_env_utils.py` | Shared environment parsing helpers used by multiple config modules. | support-layer |
| `src/fleet_rlm/cli.py` | `fleet-rlm` Typer entrypoint; initializes Hydra config and registers serve/init commands. | service-surface |
| `src/fleet_rlm/config.py` | Pydantic application config schema shared by CLI and runner assembly. | support-layer |
| `src/fleet_rlm/execution_limits.py` | Shared payload-size and recursion guards for websocket execution/observability paths. | support-layer |
| `src/fleet_rlm/fleet_cli.py` | `fleet` launcher for the standalone terminal chat experience, with a fast path to `fleet web`. | service-surface |
| `src/fleet_rlm/logging.py` | Structlog-based logging bootstrap for app processes. | support-layer |
| `src/fleet_rlm/runners.py` | High-level runtime assembly and convenience runners around the chat agent and long-context flows. | runtime-critical |
| `src/fleet_rlm/AGENTS.md` | Package-scoped contributor guidance for `src/fleet_rlm`. | developer-tooling |
| `src/fleet_rlm/py.typed` | Packaging marker for typed distributions. | support-layer |

### Directories

| Path | Files | Primary role | Classification |
| --- | ---: | --- | --- |
| `src/fleet_rlm/core/` | 11 | Modal sandbox bridge, driver protocol, LM/bootstrap helpers | runtime-critical |
| `src/fleet_rlm/react/` | 24 | Agent orchestration, tool surface, streaming, validation, delegation | runtime-critical |
| `src/fleet_rlm/server/` | 43 | FastAPI app factory, auth, websocket runtime, persistence wiring | runtime-critical |
| `src/fleet_rlm/db/` | 5 | Neon/Postgres models, engine, repository | runtime-critical |
| `src/fleet_rlm/mcp/` | 2 | FastMCP surface built on top of shared runners | service-surface |
| `src/fleet_rlm/analytics/` | 6 | PostHog lifecycle, sanitization, trace context for DSPy calls | support-layer |
| `src/fleet_rlm/chunking/` | 5 | Pure chunking strategies used by long-context and document tooling | support-layer |
| `src/fleet_rlm/models/` | 2 | Shared runtime and streaming datatypes | support-layer |
| `src/fleet_rlm/utils/` | 5 | Modal helpers, regex helpers, scaffold installers, compatibility shims | support-layer |
| `src/fleet_rlm/conf/` | 2 | Hydra config package and default config file | support-layer |
| `src/fleet_rlm/cli_commands/` | 3 | Extracted Typer command registration for `init` and server surfaces | service-surface |
| `src/fleet_rlm/terminal/` | 5 | Standalone terminal chat runtime, slash commands, shell UI | service-surface |
| `src/fleet_rlm/ui/` | 21 | Packaged frontend build artifacts for installed distributions | service-surface |
| `src/fleet_rlm/_scaffold/` | 36 | Bundled skills, agents, hooks, and team templates installed by `fleet-rlm init` | developer-tooling |

## System Surfaces at a Glance

### `fleet-rlm` CLI

- Entry: `src/fleet_rlm/cli.py`
- Config bootstrap: `fleet_rlm.conf` + `src/fleet_rlm/config.py`
- Registered surfaces:
  - `chat` for in-process terminal chat
  - `serve-api` for FastAPI
  - `serve-mcp` for FastMCP
  - `init` for scaffold installation

### `fleet` terminal command

- Entry: `src/fleet_rlm/fleet_cli.py`
- Uses the same config initialization path as `fleet-rlm`
- Delegates to:
  - `src/fleet_rlm/terminal/chat.py` for terminal chat
  - `fleet-rlm serve-api` when launched as `fleet web`

### FastAPI server

- Entry: `src/fleet_rlm/server/main.py:create_app`
- Responsibilities:
  - initialize auth, database, analytics client, planner/delegate LMs
  - register HTTP and websocket routers
  - mount the built frontend SPA when a UI bundle is available

### FastMCP server

- Entry: `src/fleet_rlm/mcp/server.py:create_mcp_server`
- Wraps the same shared runners and chat-agent builder used elsewhere
- Exposes tool-call surfaces such as `chat_turn`, `analyze_long_document`, and memory utilities

### Packaged SPA

- Packaged assets live in `src/fleet_rlm/ui/dist`
- `server/main.py:_resolve_ui_dist_dir` prefers a local `src/frontend/dist` build in source checkouts, then falls back to the packaged assets

## Directory-by-Directory Map

### `src/fleet_rlm/core/` (`runtime-critical`)

**Purpose**

- Own the host-side bridge to Modal sandboxes and the low-level protocol used to execute code, manage volumes, and resolve planner/delegate LMs.

**Important files and entrypoints**

- `interpreter.py`: `ModalInterpreter`, the main facade used by runners and the ReAct agent
- `driver.py`: sandbox-side driver entry and request/response loop
- `driver_factories.py`: driver creation helpers
- `config.py`: planner/delegate LM bootstrap from env and `.env`
- `llm_tools.py`, `sandbox_tools.py`, `volume_tools.py`, `volume_ops.py`, `session_history.py`: helper layers around sandbox state and file/volume operations

**Key classes/functions**

- `ModalInterpreter`
- `sandbox_driver`
- `configure_planner_from_env`, `get_planner_lm_from_env`, `get_delegate_lm_from_env`

**Dependencies in / out**

- Incoming: `runners.py`, `react/agent.py`, `utils/modal.py`, `server/main.py`
- Outgoing: Modal runtime, DSPy LM configuration, sandbox tool helpers, volume/session helpers

**Notes**

- This package is the most important boundary between planning/orchestration code and the execution substrate.
- It is also the most fragile place to refactor because request transport, retry behavior, lifecycle, and output shaping are intertwined.

### `src/fleet_rlm/react/` (`runtime-critical`)

**Purpose**

- Implement the top-level agent runtime and the ReAct/DSPy orchestration layer used by terminal, web, and MCP surfaces.

**Important files and entrypoints**

- `agent.py`: `RLMReActChatAgent`, the canonical chat orchestrator
- `streaming.py`: event translation from runtime activity into `StreamEvent`s
- `streaming_citations.py`, `streaming_context.py`: citation/attachment/context helpers for streamed results
- `commands.py`: slash/tool command registry and execution dispatch
- `signatures.py`: DSPy signatures for long-context, citations, memory, clarification, and related tasks
- `delegate_sub_agent.py`: child RLM orchestration
- `tool_delegation.py`: dynamic tool dispatch helpers
- `validation.py`: post-processing and response validation
- `rlm_runtime_modules.py`, `runtime_factory.py`: cached runtime module construction
- `tools/`: concrete tool implementations and tool-list assembly

#### `src/fleet_rlm/react/tools/`

- `__init__.py`: canonical tool-list assembly and shared payload helpers
- `document.py`, `filesystem.py`, `chunking.py`: document ingestion and chunking-facing tools
- `sandbox.py`, `sandbox_helpers.py`: sandbox execution/file state helpers
- `delegate.py`: delegated sub-agent tool surface
- `memory_intelligence.py`: memory tree/audit/proposal helpers

**Key classes/functions**

- `RLMReActChatAgent`
- `build_tool_list`, `list_react_tool_names`
- `execute_command`
- `spawn_delegate_sub_agent`, `spawn_delegate_sub_agent_async`

**Dependencies in / out**

- Incoming: `runners.py`, `terminal/chat.py`, websocket runtime, MCP server
- Outgoing: `core/`, `models/streaming.py`, `chunking/`, `analytics/`, `db/` via higher-level server paths

**Notes**

- `react/` is the behavioral heart of the product.
- The agent/tool/streaming split is conceptually sound, but several files remain large enough that following one turn requires jumping across many modules.

### `src/fleet_rlm/server/` (`runtime-critical`)

**Purpose**

- Own the FastAPI app, auth, dependency containers, websocket turn execution, and persistence/event wiring for the web runtime.

**Important files and entrypoints**

- `main.py`: app factory, lifespan, LM initialization, router registration, SPA mounting
- `config.py`: `ServerRuntimeConfig`
- `deps.py`: shared `ServerState`
- `middleware.py`: app middleware bootstrap
- `runtime_settings.py`: environment and runtime-setting resolution
- `routers/`: HTTP and websocket route surfaces
- `execution/`: observability/event emission and execution step building
- `auth/`: auth provider abstraction
- `schemas/`: request/response schema modules

#### `src/fleet_rlm/server/auth/`

- `build_auth_provider` selects auth implementation
- dev and Entra-style provider layers keep auth concerns out of the websocket loop

#### `src/fleet_rlm/server/execution/`

- `events.py`: execution event types and emission helpers
- `step_builder.py`: maps streamed activity into persisted execution-step structures
- `step_builder_extractors.py`, `step_builder_mapping.py`: extraction/mapping helpers already split out from the core builder
- `sanitizer.py`: payload size and safety cleanup before persistence/transport

#### `src/fleet_rlm/server/routers/`

- HTTP routers: `auth.py`, `health.py`, `runtime.py`, `sessions.py`
- `ws/`: websocket-first chat runtime

#### `src/fleet_rlm/server/routers/ws/`

- `api.py`: websocket entrypoints
- `chat_runtime.py`: runtime bootstrap per connection
- `message_loop.py`: inbound message parsing and session switching
- `streaming.py`: per-turn execution and outbound event flow
- `commands.py`: command routing for websocket frames
- `lifecycle.py`: lifecycle persistence/event emission
- `session.py`, `session_store.py`: session state persistence
- `repl_hook.py`, `helpers.py`: supporting utilities

**Key classes/functions**

- `create_app`
- `ServerRuntimeConfig`
- `ExecutionEventEmitter`
- `ExecutionStepBuilder`

**Dependencies in / out**

- Incoming: `cli_commands/serve_cmds.py`, `fleet_cli.py` via `fleet web`
- Outgoing: `db/`, `react/`, `models/`, `analytics/`, packaged `ui/`

**Notes**

- The server package is modularized well enough that responsibilities are visible.
- The cost is comprehension overhead: the websocket path spans many small files before control reaches the agent and then fans back out through execution persistence.

### `src/fleet_rlm/db/` (`runtime-critical`)

**Purpose**

- Provide typed persistence for tenants, users, sessions, runs, steps, runtime artifacts, and related control-plane data.

**Important files and entrypoints**

- `models.py`: SQLAlchemy model definitions
- `repository.py`: `FleetRepository`, the main data-access façade
- `engine.py`: connection/bootstrap helpers
- `types.py`: shared DB-facing types

**Key classes/functions**

- `DatabaseManager`
- `FleetRepository`

**Dependencies in / out**

- Incoming: `server/main.py`, websocket/session/execution flows
- Outgoing: Neon/Postgres, SQLAlchemy, migration-backed schema in `migrations/`

**Notes**

- The repository layer is the canonical persistence boundary for the live server.
- `db/models.py` is large because it centralizes the schema; `repository.py` is large because it centralizes many workflows in one façade.

### `src/fleet_rlm/mcp/` (`service-surface`)

**Purpose**

- Offer the runtime as an MCP tool server without re-implementing agent logic.

**Important files and entrypoints**

- `server.py`: `MCPRuntimeConfig` and `create_mcp_server`

**Key classes/functions**

- `MCPRuntimeConfig`
- `create_mcp_server`

**Dependencies in / out**

- Incoming: `cli_commands/serve_cmds.py`
- Outgoing: `runners.py`, `react/agent.py`, `core/config.py`

**Notes**

- This package is intentionally thin and should stay thin.
- Its value comes from reusing `runners.py` and the shared chat-agent builder instead of growing a second orchestration stack.

### `src/fleet_rlm/analytics/` (`support-layer`)

**Purpose**

- Track DSPy LM activity into PostHog with safe payload shaping and per-runtime trace context.

**Important files and entrypoints**

- `__init__.py`: `configure_analytics`
- `config.py`: `PostHogConfig`
- `client.py`: singleton PostHog client lifecycle
- `posthog_callback.py`: DSPy callback implementation
- `trace_context.py`: trace and distinct-id contextvars
- `sanitization.py`: redact and truncate helpers

**Key classes/functions**

- `PostHogConfig`
- `PostHogLLMCallback`
- `configure_analytics`

**Dependencies in / out**

- Incoming: package root exports, server startup, DSPy configuration paths
- Outgoing: PostHog SDK, `dspy.settings.callbacks`, runtime env parsing

**Notes**

- Analytics is intentionally optional and best-effort.
- The package is cohesive: config, sanitization, context, and emission all stay near one another.

### `src/fleet_rlm/chunking/` (`support-layer`)

**Purpose**

- Provide pure, stdlib-only chunking functions that can run both host-side and inside the sandbox.

**Important files and entrypoints**

- `size.py`: fixed-size chunking
- `headers.py`: markdown/header-aware chunking
- `timestamps.py`: log/timestamp chunking
- `json_keys.py`: top-level JSON key chunking

**Key classes/functions**

- `chunk_by_size`
- `chunk_by_headers`
- `chunk_by_timestamps`
- `chunk_by_json_keys`

**Dependencies in / out**

- Incoming: `react/tools/document.py`, long-context workflows, notebooks/tests
- Outgoing: stdlib only

**Notes**

- This is a clean low-risk package with a strong boundary.
- It is a good example of code that is easy to inject into the sandbox because it avoids runtime-heavy dependencies.

### `src/fleet_rlm/models/` (`support-layer`)

**Purpose**

- Define shared runtime/event models used by interactive CLI and streaming-oriented flows.

**Important files and entrypoints**

- `streaming.py`: stream events, turn state, transcript/command/result models
- `__init__.py`: public exports

**Key classes/functions**

- `ProfileConfig`
- `SessionConfig`
- `CommandResult`
- `TranscriptEvent`
- `StreamEvent`
- `TurnState`

**Dependencies in / out**

- Incoming: `terminal/`, `react/streaming.py`, websocket/session rendering paths
- Outgoing: Pydantic/dataclasses only

**Notes**

- `TurnState.apply` is one of the clearer pieces of state-folding logic in the repo.
- This package is a good candidate to remain minimal and contract-focused.

### `src/fleet_rlm/utils/` (`support-layer`)

**Purpose**

- Hold small cross-cutting helpers and packaged scaffold installation logic.

**Important files and entrypoints**

- `modal.py`: local Modal config/bootstrap helpers and interpreter creation
- `regex.py`: shared regex extraction helper
- `scaffold.py`: enumerate and install packaged skills/agents/teams/hooks from `_scaffold`
- `tools.py`: legacy compatibility shim that re-exports modal and regex helpers

**Key classes/functions**

- `get_default_volume_name`, `get_workspace_volume_name`, `ensure_volume_exists`
- `create_interpreter`
- `regex_extract`
- `get_scaffold_dir`, `list_skills`, `install_all`

**Dependencies in / out**

- Incoming: terminal settings flow, CLI init flow, compatibility imports
- Outgoing: `core/`, `_scaffold/`, filesystem

**Notes**

- `utils/tools.py` is intentionally a shim and should keep shrinking over time.
- `utils/scaffold.py` is the operational bridge between packaged assets and user installation targets.

### `src/fleet_rlm/conf/` (`support-layer`)

**Purpose**

- Provide the Hydra config package used by `cli.py` and `fleet_cli.py`.

**Important files and entrypoints**

- `config.yaml`: default config values
- `__init__.py`: marks the package for Hydra import resolution

**Dependencies in / out**

- Incoming: `cli.py:_initialize_config`
- Outgoing: `config.py::AppConfig`

**Notes**

- This package is intentionally small and declarative.
- The main boundary worth preserving is that Hydra composition resolves into the Pydantic `AppConfig` schema, not into ad hoc dicts throughout the codebase.

### `src/fleet_rlm/cli_commands/` (`service-surface`)

**Purpose**

- Keep Typer command registration modular without moving core runtime logic out of the shared packages.

**Important files and entrypoints**

- `init_cmd.py`: register scaffold bootstrap command
- `serve_cmds.py`: register `serve-api` and `serve-mcp`

**Key classes/functions**

- `register_init_command`
- `register_serve_commands`

**Dependencies in / out**

- Incoming: `cli.py`
- Outgoing: `utils/scaffold.py`, `server/main.py`, `mcp/server.py`

**Notes**

- This package is thin by design.
- It should stay focused on CLI wiring, not become another place where runtime settings or agent construction logic are duplicated.

### `src/fleet_rlm/terminal/` (`service-surface`)

**Purpose**

- Provide the standalone terminal chat runtime for developers who want an in-process shell instead of the web UI.

**Important files and entrypoints**

- `chat.py`: terminal session lifecycle, agent creation, prompt loop
- `commands.py`: slash-command parsing and command execution
- `settings.py`: interactive settings/modality helpers
- `ui.py`: prompt-toolkit and rich rendering helpers

**Key classes/functions**

- `run_terminal_chat`
- `TerminalChatOptions`
- `_TerminalChatSession`
- `handle_slash_command`

**Dependencies in / out**

- Incoming: `cli.py`, `fleet_cli.py`
- Outgoing: `runners.py`, `react/commands.py`, `models/`, `utils/modal.py`

**Notes**

- The package already has a partial split between session control, slash commands, settings, and rendering.
- It still carries substantial controller logic in `chat.py` and a large command dispatch surface in `commands.py`.

### `src/fleet_rlm/ui/` (`service-surface`)

**Purpose**

- Ship the built SPA inside the Python distribution so installed server deployments can serve a frontend without a colocated `src/frontend` checkout.

**Important files and entrypoints**

- `dist/index.html`
- `dist/assets/*`
- packaged branding/favicon assets

**Dependencies in / out**

- Incoming: `server/main.py:_resolve_ui_dist_dir` and `_mount_spa`
- Outgoing: none; this is an asset package

**Notes**

- `ui/` is not a source-of-truth frontend workspace.
- The source-of-truth web app still lives in `src/frontend/`; `src/fleet_rlm/ui/` is the packaged distribution fallback.

### `src/fleet_rlm/_scaffold/` (`developer-tooling`)

**Purpose**

- Bundle reusable templates and assets installed by `fleet-rlm init`.

**Important subdirectories**

- `agents/`: Markdown agent definitions such as `rlm-orchestrator.md` and `rlm-subcall.md`
- `skills/`: packaged skill bundles including `rlm`, `rlm-debug`, `rlm-batch`, `rlm-execute`, `modal-sandbox`, and long-context helpers
- `teams/`: team templates and inbox assets
- `hooks/`: example local hook wiring

**Dependencies in / out**

- Incoming: `utils/scaffold.py`, `cli_commands/init_cmd.py`
- Outgoing: user install targets like `~/.claude`, not the live runtime

**Notes**

- `_scaffold/` is operationally important for developer bootstrap, but it is not part of request-time backend execution.
- Its scripts can have their own complexity, but they should not shape runtime architecture decisions.

## End-to-End Runtime Flow

```text
                                 +----------------------+
                                 |  src/frontend/       |
                                 |  source web app      |
                                 +----------+-----------+
                                            |
                                            | build
                                            v
 +------------------+              +----------------------+
 | fleet-rlm CLI    |              | src/fleet_rlm/ui/    |
 | cli.py           |              | packaged dist assets |
 +--------+---------+              +----------+-----------+
          |                                   ^
          | Hydra + AppConfig                 | mount fallback
          v                                   |
 +------------------+              +----------+-----------+
 | conf/config.yaml |              | server/main.py       |
 | config.py        |              | create_app()         |
 +--------+---------+              +----+-----------+-----+
          |                             |           |
          |                             |           +--------------------+
          |                             |                                |
          v                             v                                v
 +------------------+         +--------------------+          +--------------------+
 | runners.py       |<------->| ServerState / deps |<-------->| db/repository.py   |
 | agent assembly   |         | auth / LM / emit   |          | db/models.py       |
 +--------+---------+         +---------+----------+          +--------------------+
          |                             |
          |                             | websocket / HTTP
          v                             v
 +---------------------------+   +---------------------------+
 | react/agent.py            |<->| server/routers/ws/*       |
 | RLMReActChatAgent         |   | chat runtime + streaming  |
 +------------+--------------+   +-------------+-------------+
              |                                |
              | tools / commands               | execution events
              v                                v
 +---------------------------+        +------------------------------+
 | react/tools/*             |------->| server/execution/*           |
 | document/filesystem/      |        | sanitizer + step builder     |
 | sandbox/delegate/memory   |        +------------------------------+
 +------------+--------------+
              |
              | execute / delegate / load docs
              v
 +---------------------------+
 | core/interpreter.py       |
 | ModalInterpreter          |
 +------------+--------------+
              |
              v
 +---------------------------+
 | core/driver.py            |
 | sandbox tools / volumes   |
 +------------+--------------+
              |
              +------------------------------+
              |                              |
              v                              v
 +---------------------------+    +---------------------------+
 | chunking/*                |    | analytics/*               |
 | pure text splitters       |    | PostHog + trace context   |
 +---------------------------+    +---------------------------+

 Additional service surfaces:
 - fleet_cli.py -> terminal/chat.py -> runners.py -> same react/core runtime
 - mcp/server.py -> FastMCP tools -> runners.py / build_react_chat_agent()
 - cli_commands/init_cmd.py -> utils/scaffold.py -> _scaffold/* assets
```

## Stable Boundaries to Preserve

These are the seams future simplification work should preserve even if file layout changes.

### 1. Config bootstrap stays centralized

- Hydra config should continue to resolve through `fleet_rlm.conf` into `config.py::AppConfig`.
- Server-only runtime settings should remain isolated in `server/config.py` and `server/runtime_settings.py`.

### 2. The agent runtime stays the single behavioral core

- `RLMReActChatAgent` and `react/tools/*` should remain the canonical runtime used by terminal, web, and MCP surfaces.
- Service surfaces should assemble or wrap the runtime, not fork orchestration logic.

### 3. Interpreter-to-sandbox remains a hard boundary

- `core/interpreter.py` should stay the single façade above the driver protocol.
- Sandbox driver/tool code should stay separated from server/router concerns.

### 4. Websocket transport and persisted execution events remain aligned

- The event contract among `react/streaming.py`, `server/routers/ws/*`, `server/execution/*`, and the frontend adapter should stay consistent.
- Changes to stream payload semantics should be treated as backend/frontend contract changes, not local refactors.

### 5. Persistence stays behind the repository layer

- Server flows should continue to depend on `FleetRepository` rather than spread SQLAlchemy usage through routers or agent code.

### 6. Packaged UI and scaffold assets stay clearly non-source-of-truth

- `src/fleet_rlm/ui/` should remain a packaged artifact fallback.
- `_scaffold/` should remain install-time content, not runtime code.

## Simplification Hotspots

These are the highest-leverage simplification targets based on current size, symbol density, and cross-module coupling.

### 1. `src/fleet_rlm/core/interpreter.py`

**Why it is complex**

- It concentrates lifecycle, transport, retries, payload shaping, async/sync execution, and sandbox state management behind one façade.

**Best incremental direction**

- Split protocol transport/request-response handling from lifecycle/configuration state.
- Keep `ModalInterpreter` as the stable public façade while extracting:
  - request serialization and response normalization
  - process/session lifecycle
  - volume/session-history helpers

**Why this matters**

- This is the narrowest high-value place to reduce backend complexity without changing public runtime behavior.

### 2. `src/fleet_rlm/react/streaming.py` plus adjacent streaming helpers

**Why it is complex**

- Stream translation, fallback handling, nested tool/delegate event shaping, and final payload assembly are spread across multiple modules.

**Best incremental direction**

- Separate pure event translation from orchestration and buffering.
- Treat streaming as a pipeline:
  - raw runtime observations
  - normalized internal event objects
  - transport-ready websocket payloads

**Why this matters**

- The websocket/frontend contract is one of the easiest places to regress accidentally; smaller translation layers make that contract easier to audit.

### 3. Runtime assembly drift across `runners.py`, `cli.py`, `cli_commands/serve_cmds.py`, `server/main.py`, and `mcp/server.py`

**Why it is complex**

- Runtime options and builder arguments are repeated across multiple entry surfaces.
- The same underlying knobs are mapped slightly differently depending on CLI, server, or MCP path.

**Best incremental direction**

- Extract one internal runtime-options normalizer or config adapter used by all three surfaces.
- Keep public entrypoints separate, but centralize the mapping from `AppConfig` / `ServerRuntimeConfig` into agent/interpreter construction parameters.

**Why this matters**

- This reduces drift risk whenever a new guardrail, delegate, or budget option is added.

### 4. `src/fleet_rlm/terminal/` controller split is incomplete

**Why it is complex**

- `chat.py`, `commands.py`, and `ui.py` are already separated, but command authorization, session control, rendering, and runner shortcuts still overlap conceptually.

**Best incremental direction**

- Make `chat.py` own session state only.
- Make `commands.py` a pure registry/dispatch layer.
- Keep `ui.py` renderer-only and move filesystem/runner side effects fully out of it.

**Why this matters**

- The terminal surface is not the primary product path, but it is large enough that it creates real maintenance noise if left half-modularized.

### 5. `src/fleet_rlm/db/repository.py` and `src/fleet_rlm/db/models.py`

**Why it is complex**

- The schema is centralized in one large file and the repository acts as a broad façade over many workflows.

**Best incremental direction**

- Keep one SQLAlchemy metadata root, but split repository responsibilities by domain slice such as sessions, runs/execution, and artifacts/runtime settings.
- If model growth continues, group model declarations by aggregate with a shared import barrel rather than one giant file.

**Why this matters**

- This reduces onboarding cost for persistence changes without forcing a schema redesign.

### 6. `src/fleet_rlm/utils/scaffold.py` and `_scaffold/`

**Why it is complex**

- Install-time enumeration, metadata parsing, copying rules, and asset categorization all live in one operational utility layer.

**Best incremental direction**

- Separate asset discovery/indexing from install/copy operations.
- Keep `_scaffold/` content static and avoid mixing business logic into bundled scripts unless the script is intentionally reusable outside installation.

**Why this matters**

- This keeps developer bootstrap features from leaking complexity back into the main utility layer.

### 7. Compatibility and shim surfaces in `utils/tools.py` and lazy package exports

**Why it is complex**

- Backward compatibility is useful, but every shim makes the package map harder to read.

**Best incremental direction**

- Continue converging callers on canonical modules such as `utils/modal.py` and `utils/regex.py`.
- Keep shims documented and prune them once tests and consumers no longer require them.

**Why this matters**

- Fewer alias paths make architectural ownership much easier to explain and enforce.

## Practical Simplification Sequence

If the goal is to reduce cognitive load without destabilizing behavior, this is the safest order:

1. Extract internal helpers from `core/interpreter.py` while preserving the `ModalInterpreter` surface.
2. Normalize the streaming pipeline across `react/streaming.py` and adjacent event/citation helpers.
3. Centralize runtime option mapping shared by CLI, FastAPI, and FastMCP entrypoints.
4. Finish the terminal package split so it is clearly session/controller/view.
5. Break up DB repository responsibilities once runtime assembly and streaming are easier to follow.

## Bottom Line

The package architecture already has a sound high-level shape:

- shared config/bootstrap
- one canonical agent runtime
- one interpreter boundary
- one server/runtime state container
- one repository boundary

The main readability problem is not missing structure. It is that several of the most important modules are still too broad, and the runtime story is distributed across many entry surfaces. The best simplification strategy is therefore to keep the existing package seams, but make the heavy modules inside those seams smaller and more explicit.
