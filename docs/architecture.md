# Architecture Overview

This document describes the maintained architecture for `fleet-rlm`.
The primary product path is now the shared DSPy ReAct + recursive `dspy.RLM`
runtime with Daytona as the default interpreter backend. Modal remains a
supported backend for the same runtime architecture.

## Current Runtime Status

- The primary product runtime is the shared `RLMReActChatAgent` plus recursive
  `dspy.RLM` stack described below.
- Daytona is the default interpreter backend for that shared runtime in the
  workspace product.
- Modal remains supported for compatibility, terminal flows, and backend parity
  where the Modal interpreter is still the selected execution backend.
- The Web UI still uses the same `/api/v1/ws/chat` surface; `runtime_mode`
  selects interpreter/backend details rather than swapping in a separate chat
  orchestrator.
- The only supported Daytona CLI surface is `fleet-rlm daytona-smoke --repo ... [--ref ...]` for native Daytona validation.
- `fleet-rlm daytona-smoke` is the required first-step validation path and now emits phase-aware diagnostics for config, sandbox bootstrap, driver startup, execution, and cleanup.
- The Daytona pilot now splits cleanly into an async Daytona interpreter core and a thin product adapter. The interpreter owns persistent sandbox execution, prompt-object storage, typed `SUBMIT`, and workspace-native helpers while the shared ReAct/RLM runtime owns chat orchestration and recursion.
- The Daytona interpreter helper surface is environment-native: workspace inspection and chunking happen inside the persistent sandbox driver via `read_file_slice`, `grep_repo`, `chunk_text`, and `chunk_file`, long task/observation payloads are externalized there through `store_prompt`, `list_prompts`, and `read_prompt_slice`, and local document ingestion is shared with the rest of the backend through `document_ingestion.py`; host callbacks bridge semantic `llm_query` work back to the planner LM.
- In the Web UI integration, Daytona-backed workspace execution is the primary
  path. Optional `repo_url`, optional `context_paths`, optional `repo_ref` when
  a repo is configured, and optional `batch_concurrency` remain available
  Daytona request controls. Daytona websocket requests reject request-side
  `max_depth`, while streamed runtime metadata still includes
  `runtime.max_depth` as read-only execution state. Image-only or scanned PDFs
  fail with an explicit OCR-required context-stage diagnostic rather than
  silently degrading.

## System Architecture Diagram

The following diagram shows the complete system architecture with all major components and their relationships:

```mermaid
graph TB
    subgraph ENTRY["Entry Points"]
        CLI_FLEET["fleet CLI<br/>(cli/main.py)"]
        CLI_RLM["fleet-rlm CLI<br/>(cli/fleet_cli.py)"]
        WEB_UI["Web UI<br/>(frontend/)"]
        MCP_CLIENT["MCP Client<br/>(integrations/mcp/server.py)"]
    end

    subgraph SERVER["Service Layer (api/)"]
        MAIN["FastAPI App<br/>(main.py)"]
        ROUTERS["Routers<br/>(routers/)"]
        AUTH["Auth<br/>(auth/)"]
        WS["WebSocket<br/>(routers/ws/)"]
    end

    subgraph ORCHESTRATION["Orchestration Layer (runtime/agent + runtime/execution)"]
        AGENT["RLMReActChatAgent<br/>(runtime/agent/chat_agent.py)"]
        SIGNATURES["Signatures<br/>(runtime/agent/signatures.py)"]
        STREAMING["Streaming<br/>(runtime/execution/streaming.py, streaming_context.py)"]
        DELEGATE["Recursive Runtime<br/>(runtime/agent/recursive_runtime.py)"]
    end

    subgraph TOOLS["Tools Layer (runtime/tools/)"]
        TOOL_DOCUMENT["document.py"]
        TOOL_SANDBOX["sandbox.py<br/>(sandbox, RLM, memory)"]
        TOOL_CHUNKING["chunking.py"]
        TOOL_FILESYSTEM["filesystem.py"]
    end

    subgraph EXECUTION["Execution Layer (runtime/)"]
        INTERPRETER["Interpreter Backends<br/>(runtime/execution/interpreter.py + integrations/providers/daytona/)"]
        DRIVER["Sandbox Driver<br/>(core_driver.py)"]
        LLM_TOOLS["LLM Tools<br/>(llm_tools.py)"]
        VOLUME_OPS["Volume Operations<br/>(modal_volumes.py)"]
        DRIVER_ASSETS["Driver Assets<br/>(sandbox_assets.py, output_utils.py)"]
    end

    subgraph MODAL["Modal Cloud"]
        SANDBOX["Modal Sandbox"]
        VOLUME["Modal Volume"]
    end

    subgraph PERSISTENCE["Persistence Layer"]
        DB["Database (integrations/database/)<br/>engine.py, models.py, repository.py"]
        NEON[("Neon Postgres<br/>(RLS-enabled)")]
        MLFLOW["MLflow Integration<br/>(integrations/observability/)"]
        POSTHOG["PostHog Callback<br/>(integrations/observability/)"]
    end

    subgraph LLM["Language Models"]
        PLANNER_LM["Planner LM<br/>(DSPy)"]
        DELEGATE_LM["Delegate LM<br/>(DSPy/RLM)"]
    end

    %% Entry point connections
    CLI_FLEET --> AGENT
    CLI_RLM --> AGENT
    CLI_RLM --> MAIN
    WEB_UI --> WS
    MCP_CLIENT --> MAIN

    %% Server layer
    MAIN --> ROUTERS
    ROUTERS --> AUTH
    ROUTERS --> WS
    WS --> AGENT

    %% Orchestration connections
    AGENT --> SIGNATURES
    AGENT --> STREAMING
    AGENT --> DELEGATE
    AGENT --> TOOLS

    %% Tools layer
    TOOLS --> INTERPRETER

    %% Execution connections
    INTERPRETER --> DRIVER
    INTERPRETER --> LLM_TOOLS
    INTERPRETER --> VOLUME_OPS
    DRIVER --> SANDBOX
    DRIVER --> VOLUME
    DRIVER_ASSETS --> SANDBOX

    %% Persistence connections
    MAIN --> DB
    DB --> NEON
    AGENT --> MLFLOW
    AGENT --> POSTHOG

    %% LLM connections
    AGENT --> PLANNER_LM
    DELEGATE --> DELEGATE_LM

    %% Styling
    classDef entryNode fill:#e1f5fe,stroke:#01579b
    classDef serverNode fill:#fff3e0,stroke:#e65100
    classDef orchestrationNode fill:#e8f5e9,stroke:#1b5e20
    classDef toolsNode fill:#fce4ec,stroke:#880e4f
    classDef executionNode fill:#f3e5f5,stroke:#4a148c
    classDef modalNode fill:#e0f2f1,stroke:#004d40
    classDef persistenceNode fill:#fff8e1,stroke:#f57f17
    classDef llmNode fill:#ede7f6,stroke:#311b92

    class CLI_FLEET,CLI_RLM,WEB_UI,MCP_CLIENT entryNode
    class MAIN,ROUTERS,AUTH,WS serverNode
    class AGENT,SIGNATURES,STREAMING,DELEGATE orchestrationNode
    class TOOL_DOCUMENT,TOOL_SANDBOX,TOOL_CHUNKING,TOOL_FILESYSTEM toolsNode
    class INTERPRETER,DRIVER,LLM_TOOLS,VOLUME_OPS,DRIVER_ASSETS executionNode
    class SANDBOX,VOLUME modalNode
    class DB,NEON,MLFLOW,POSTHOG persistenceNode
    class PLANNER_LM,DELEGATE_LM llmNode
```

## Entry Points

| Entry Point | Source File                   | Description                                                                                                       |
| ----------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `fleet`     | `src/fleet_rlm/cli/main.py`                    | Primary interactive chat launcher. Supports `fleet web` for the Web UI.                                           |
| `fleet-rlm` | `src/fleet_rlm/cli/fleet_cli.py`               | Full CLI with `chat`, `serve-api`, `serve-mcp`, `init`, and `daytona-smoke`. |
| Web UI      | `src/frontend/`               | React/TypeScript frontend served by FastAPI at `http://0.0.0.0:8000`.                                             |
| MCP Server  | `src/fleet_rlm/integrations/mcp/server.py`   | Model Context Protocol server for Claude Desktop integration.                                                      |

## Core Layers

### 1. Entry Points Layer

Entry points define how users interact with the system:

- **`cli/main.py`**: Lightweight wrapper that provides `fleet` for terminal
  chat and `fleet web` for Web UI launch.
- **`cli/fleet_cli.py`**: Full Typer-based CLI with subcommands:
  - `chat`: Standalone interactive terminal chat
  - `serve-api`: FastAPI server for HTTP/WebSocket API
  - `serve-mcp`: MCP server for Claude Desktop integration
  - `init`: Bootstrap Claude Code scaffold assets
  - `daytona-smoke`: native Daytona smoke validation for repo clone + driver persistence

### 2. Orchestration Layer (`runtime/agent/` + `runtime/execution/`)

The orchestration layer manages the ReAct agent loop and streaming:

| Module                  | Purpose                                                           |
| ----------------------- | ----------------------------------------------------------------- |
| `chat_agent.py`         | `RLMReActChatAgent` - stateful conversational agent with tool use |
| `chat_turns.py`         | Turn-state accounting, prediction normalization, and turn result shaping |
| `signatures.py`         | DSPy signature definitions for agent inputs/outputs               |
| `streaming.py`          | Real-time streaming of chat turns and trajectory events           |
| `streaming_context.py`  | Context management for streaming sessions                         |
| `recursive_runtime.py`  | Spawns child `dspy.RLM` instances for recursive reasoning         |
| `commands.py`           | Built-in command dispatch (e.g., `/help`, `/reset`)               |

### 3. Tools Layer (`runtime/tools/`)

Tools provide capabilities for the ReAct agent:

| Module                   | Purpose                             |
| ------------------------ | ----------------------------------- |
| `document.py`            | Document loading and processing     |
| `sandbox.py`             | Sandbox execution, cached-runtime delegation tools, memory-intelligence tools, and persistent memory helpers |
| `chunking.py`            | Text chunking for long documents    |
| `filesystem.py`          | File system operations in sandbox   |

### 4. Execution Layer (`runtime/`)

The execution layer owns the shared interpreter/runtime infrastructure used by both Modal and Daytona:

| Module                | Purpose                                              |
| --------------------- | ---------------------------------------------------- |
| `interpreter.py`      | `ModalInterpreter` - manages Modal sandbox lifecycle |
| `core_driver.py`     | `sandbox_driver` - executes Python code in sandbox   |
| `driver_factories.py` | Factory functions for driver configuration           |
| `llm_tools.py`        | LLM-backed tools for the sandbox                     |
| `modal_volumes.py`    | Canonical Modal volume persistence and browsing helpers |
| `sandbox_assets.py`   | Bundled helper assets injected into the sandbox driver |
| `output_utils.py`     | Output redaction and stdout summarization helpers    |

### 4a. Experimental Daytona Pilot (`integrations/providers/daytona/`)

The Daytona pilot is an experimental interpreter backend for the shared runtime and is not a separate orchestration stack.

| Module                | Purpose                                                                                                             |
| --------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `types_budget.py`, `types_context.py`, `types_recursive.py`, `types_result.py`, `types_serialization.py` | Focused Daytona rollout, context, recursion, result, and serialization contracts |
| `config.py`           | Explicit native Daytona env resolution and preflight validation                                                     |
| `runtime.py`          | Direct-SDK sandbox bootstrap, repo clone orchestration, volume attach, and local context staging                    |
| `bridge.py`           | Minimal guide-style broker bridge for `llm_query`, `llm_query_batched`, custom tools, and `SUBMIT(...)`            |
| `smoke.py`            | CLI-first Daytona smoke workflow validating persistent code-interpreter state                                        |
| `interpreter.py`      | Daytona interpreter backend compatible with the shared ReAct + `dspy.RLM` flow                                      |
| `agent.py`            | Thin Daytona compatibility wrapper over the shared ReAct agent                                                      |
| `state.py`            | Daytona chat/session normalization helpers                                                                           |
| `volumes.py`          | Daytona volume browsing helpers                                                                                      |

Important scope notes:

- The pilot is workspace-centric: `--repo` is optional, `--context-path` is repeatable, and `--ref` is only valid when a repo is configured.
- Each root Daytona session uses one sandbox workspace plus one persistent Daytona code-interpreter context.
- Repo and workspace-analysis helpers are sandbox-native helper functions injected into that persistent context and survive across iterations.
- `llm_query` / `llm_query_batched` are host-side LM callbacks bridged into the sandbox through the minimal Daytona broker process. `rlm_query` / `rlm_query_batched` remain the true recursive child-RLM helpers through the shared runtime.
- The pilot still does not replace `ModalInterpreter` or the default `modal_chat` product path, but it now has its own first-class websocket runtime mode while sharing the same ReAct + `dspy.RLM` orchestration core.
- Contributors should run Daytona in this order: set `DAYTONA_API_KEY` + `DAYTONA_API_URL`, then run `fleet-rlm daytona-smoke --repo <url>` before using `daytona_pilot` in the web workspace.

### 4b. Experimental Daytona Workbench

The Daytona pilot now has a dedicated DSPy-native websocket chat agent plus a workbench inspector in `RLM Workspace`.

| Module                                                              | Purpose                                                                                                                                                                                                                         |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `integrations/providers/daytona/agent.py`                          | `DaytonaWorkbenchChatAgent` - focused Daytona-specific agent layer over the shared ReAct runtime                                                                                                                                |
| `api/schemas/core.py`                                               | Adds Daytona websocket source controls (`runtime_mode`, `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`) plus Daytona runtime readiness metadata; request-side `max_depth` is explicitly rejected for Daytona chat |
| `api/routers/ws/endpoint.py` / `api/routers/ws/session.py`          | Select the top-level websocket chat agent from the first message and bootstrap runtime/session state                                                                                                                             |
| `api/routers/ws/stream.py` / `api/routers/ws/commands.py`           | Route Daytona turns through the shared websocket session/streaming lifecycle instead of a one-shot Daytona-only branch                                                                                                          |
| `frontend/src/screens/workspace/model/chat-store.ts`                | Persists runtime selection, session identity, and runtime-specific request options in UI state                                                                                                                                 |
| `frontend/src/screens/workspace/components/workspace-composer.tsx`  | Renders runtime selection and keeps execution-mode controls Modal-only                                                                                                                                                           |
| `frontend/src/screens/workspace/workspace-screen.tsx`               | Keeps the shared chat surface visible, switches warnings by runtime, and relies on the workbench instead of a mandatory Daytona setup card                                                                                     |
| `frontend/src/screens/workspace/model/run-workbench-*`              | Dedicated analyst workbench state/UI for iterations, evidence, callbacks, prompt objects, and final output                                                                                                                      |

Important scope notes:

- The UI toggle is explicit: `Modal chat` vs `Daytona pilot`.
- `execution_mode` still applies only to the default Modal chat path.
- Daytona UI requests are Daytona-interpreter-oriented: the backend runs the shared ReAct + `dspy.RLM` flow against a Daytona-backed persistent REPL/runtime and renders workbench state from structured run events plus interpreter output.
- Daytona websocket chat now shares the same session/export/import lifecycle as Modal chat, so Daytona history persists by `session_id` and restores through the existing websocket session store.
- The workbench is now general-purpose Daytona-backed recursive reasoning rather than repo-analysis-only and currently shows:
  - task-aware runtime/task controls and status,
  - optional repo plus staged local document/directory sources or `No external sources`,
  - ordered iteration summaries instead of a recursive run tree,
  - an evidence tab for staged corpus items, cited file slices, and attachments,
  - callback, prompt-object, and final-output tabs for analyst-style drill-in.

#### Daytona Analyst Runtime Flow

```mermaid
flowchart LR
    User["User prompt"] --> Workspace["RLM Workspace chat + workbench"]
    Workspace --> WS["/api/v1/ws/chat"]
    WS --> Agent["DaytonaWorkbenchChatAgent"]
    Agent --> Shared["RLMReActChatAgent / dspy.RLM"]
    Shared --> Session["DaytonaSandboxSession"]
    Shared --> Planner["Planner LM"]
    Shared -. semantic callbacks .-> Delegate["llm_query / llm_query_batched"]
    Session --> Driver["Persistent Daytona driver"]
    Driver --> Corpus["Repo + .fleet-rlm/context corpus"]
    Shared --> Result["Shared execution + final output"]
    Result --> Tabs["Iterations / Evidence / Callbacks / Prompts / Final"]
```

#### Daytona Frontend Data Flow

```mermaid
sequenceDiagram
    participant User as User
    participant Store as chatStore
    participant WS as WebSocket
    participant Agent as DaytonaWorkbenchChatAgent
    participant Shared as RLMReActChatAgent
    participant Adapter as runWorkbenchAdapter
    participant UI as RunWorkbench

    User->>Store: submit message + optional corpus inputs
    Store->>WS: WSMessage(runtime_mode="daytona_pilot")
    WS->>Agent: aiter_chat_turn_stream(...)
    Agent->>Shared: aiter_chat_turn_stream(...)
    Shared-->>WS: status/tool_call/tool_result/final frames
    WS-->>Store: event frames
    Store->>Adapter: applyFrameToRunWorkbenchState(...)
    Adapter->>UI: hydrate iterations, evidence, callbacks, prompts, final output
```

### 5. Service Layer (`api/`)

The service layer provides HTTP and WebSocket APIs:

| Module                       | Purpose                                      |
| ---------------------------- | -------------------------------------------- |
| `main.py`                    | FastAPI application factory                  |
| `routers/runtime.py`         | Runtime settings and status endpoints        |
| `routers/sessions.py`        | Session state management                     |
| `routers/traces.py`          | MLflow trace endpoints                       |
| `routers/health.py`          | Health check endpoints (`/health`, `/ready`) |
| `routers/auth.py`            | Authentication endpoints                     |
| `routers/ws/session.py`      | WebSocket runtime/session bootstrap          |
| `routers/ws/endpoint.py`     | WebSocket API surface                        |
| `auth/`                      | Authentication middleware (dev/Entra modes)  |

### 6. Persistence Layer

| Component                                  | Purpose                                                 |
| ------------------------------------------ | ------------------------------------------------------- |
| `integrations/database/engine.py`          | Async database engine with connection pooling           |
| `integrations/database/models.py`          | SQLModel definitions for runs, steps, artifacts, memory |
| `integrations/database/repository.py`      | Repository pattern for database operations              |
| `integrations/observability/mlflow_runtime.py` | MLflow lifecycle, callbacks, and request-context wiring |
| `integrations/observability/mlflow_traces.py`  | MLflow trace lookup, feedback logging, and dataset export |
| `integrations/observability/posthog_callback.py` | PostHog telemetry callback                           |

## Data Flow Diagrams

### Chat Turn Flow

This diagram shows the complete flow of a chat turn from user message to response streaming, based on the WebSocket runtime in `src/fleet_rlm/api/routers/ws/`.

```mermaid
sequenceDiagram
    autonumber
    participant User as User
    participant WS as WebSocket<br/>(ws/endpoint.py)
    participant MsgLoop as session.py + stream.py
    participant Runtime as session.py
    participant Agent as RLMReActChatAgent<br/>(runtime/agent/chat_agent.py)
    participant StreamCtx as StreamingContext<br/>(runtime/execution/streaming_context.py)
    participant Stream as runtime/execution/streaming.py
    participant Tools as Tools<br/>(runtime/tools/)
    participant Interpreter as ModalInterpreter<br/>(interpreter.py)
    participant Modal as Modal Sandbox
    participant DB as Neon Postgres<br/>(integrations/database/repository.py)

    User->>WS: WSMessage {type: "message"}
    WS->>MsgLoop: parse_ws_message_or_send_error()
    MsgLoop->>Runtime: _prepare_chat_runtime()
    Runtime->>Runtime: resolve_admitted_identity()
    Runtime->>Runtime: _build_chat_agent_context()
    Runtime-->>MsgLoop: _PreparedChatRuntime
    MsgLoop->>Agent: agent.chat_stream(message)

    Agent->>StreamCtx: StreamingContext.from_agent(agent)
    StreamCtx-->>Agent: Immutable context snapshot
    Agent->>Stream: aiter_chat_turn_stream()

    loop ReAct Loop
        Agent->>Agent: think → act → observe
        Agent->>Tools: Execute tool call
        Tools->>Interpreter: interpreter.execute(code)
        Interpreter->>Modal: Run code in sandbox
        Modal-->>Interpreter: Execution result
        Interpreter-->>Tools: Return result
        Tools-->>Agent: Tool result
        Agent->>Stream: Emit StreamEvent
        Stream-->>WS: Stream trajectory events
        WS-->>User: Display incremental response
    end

    Agent-->>MsgLoop: Final response
    MsgLoop->>DB: persist_session_state()
    MsgLoop-->>WS: Stream complete
    WS-->>User: Turn finished
```

**Key Components:**

| Component                        | Source File                           | Role                                                      |
| -------------------------------- | ------------------------------------- | --------------------------------------------------------- |
| `parse_ws_message_or_send_error` | `ws/session.py`                       | Parse incoming WebSocket JSON into `WSMessage`            |
| `_prepare_chat_runtime`          | `ws/session.py`                       | Initialize agent with planner LM, delegate LM, repository |
| `StreamingContext`               | `runtime/execution/streaming_context.py` | Immutable snapshot of agent state for event enrichment |
| `aiter_chat_turn_stream`         | `runtime/execution/streaming.py`      | Async iterator yielding `StreamEvent` objects             |

### RLM Delegation Flow

This diagram shows how parent agents spawn child RLM instances for recursive reasoning, based on `src/fleet_rlm/runtime/agent/recursive_runtime.py`.

```mermaid
sequenceDiagram
    autonumber
    participant Parent as Parent Agent<br/>(RLMReActChatAgent)
    participant Delegate as delegate_sub_agent.py
    participant Builder as rlm_runtime_modules.py
    participant ChildCtx as Child Interpreter
    participant ChildRLM as Child dspy.RLM
    participant StreamCb as StreamEventCallback
    participant LLM as Language Model

    Parent->>Delegate: spawn_delegate_sub_agent_async(prompt, context)
    Delegate->>Delegate: _claim_delegate_slot_or_error()

    alt Delegate budget exhausted
        Delegate-->>Parent: {status: "error", error: "budget reached"}
    end

    Delegate->>Delegate: _remaining_llm_budget(agent)
    Delegate->>Delegate: _build_child_interpreter(agent)

    alt Parent has live sandbox
        Note over ChildCtx: Reuse parent interpreter
    else No live sandbox
        Note over ChildCtx: Create new ModalInterpreter
        ChildCtx->>ChildCtx: Share LLM budget with parent
    end

    Delegate->>Builder: build_recursive_subquery_rlm(interpreter, max_iters)
    Builder-->>Delegate: Child RLM module

    Delegate->>Delegate: _delegate_streaming_context(agent)
    Delegate-->>StreamCb: Create callback for trajectory events

    Delegate->>ChildRLM: dspy.streamify(child_module)
    ChildRLM->>ChildRLM: Recursive ReAct loop

    loop Child Iteration
        ChildRLM->>LLM: LLM call
        LLM-->>ChildRLM: Response
        ChildRLM->>StreamCb: Emit trajectory_step event
        StreamCb-->>Parent: Forward to parent's event stream
    end

    ChildRLM-->>Delegate: dspy.Prediction
    Delegate->>Delegate: _normalize_delegate_result()
    Delegate-->>Parent: {status: "ok", answer, trajectory, depth}
```

**Key Components:**

| Function                         | Source File                          | Purpose                                           |
| -------------------------------- | ------------------------------------ | ------------------------------------------------- |
| `spawn_delegate_sub_agent_async` | `runtime/agent/recursive_runtime.py` | Main entry point for delegation                   |
| `claim_delegate_slot_or_error`   | `runtime/agent/delegation_policy.py` | Enforce `delegate_max_calls_per_turn` limit       |
| `build_child_interpreter`        | `runtime/agent/delegation_policy.py` | Create or reuse ModalInterpreter for child        |
| `build_recursive_subquery_rlm`   | `runtime/models/rlm_runtime_modules.py` | Construct `dspy.RLM` module with sandbox tools |
| `_delegate_streaming_context`    | `runtime/agent/recursive_runtime.py` | Build `StreamingContext` for child depth tracking |

**Depth and Budget Controls:**

- `max_depth`: Maximum recursion depth (default: 2)
- `delegate_max_calls_per_turn`: Maximum delegate calls per parent turn (default: 8)
- `max_llm_calls`: LLM call budget shared between parent and children

### Sandbox Execution Flow

This diagram shows how code execution flows from tool calls through the ModalInterpreter to the sandbox driver, based on `src/fleet_rlm/runtime/execution/interpreter.py` and `core_driver.py`.

```mermaid
sequenceDiagram
    autonumber
    participant Tool as Tool<br/>(runtime/tools/)
    participant Interp as ModalInterpreter<br/>(interpreter.py)
    participant VolOps as VolumeOpsMixin<br/>(modal_volumes.py)
    participant Driver as sandbox_driver<br/>(driver.py)
    participant Sandbox as Modal.Sandbox
    participant Volume as Modal.Volume

    Tool->>Interp: interpreter.execute(code, variables, tool_names)
    Interp->>Interp: _ensure_started()

    alt Sandbox not running
        Interp->>Sandbox: modal.Sandbox.create(image, secrets, timeout)
        Sandbox-->>Interp: Sandbox instance
        Interp->>Interp: Start driver process
        Interp->>Driver: stdin: JSON command
    end

    Interp->>Interp: Build JSON command
    Note over Interp: {code, variables, tool_names,<br/>output_names, execution_profile}

    Interp->>Driver: stdin: JSON command
    Driver->>Driver: Parse JSON, prepare globals
    Driver->>Driver: inject_sandbox_helpers(globals)

    Note over Driver: Inject built-ins:<br/>SUBMIT, Final, llm_query,<br/>workspace_read/write, etc.

    Driver->>Driver: exec(code, sandbox_globals)

    alt Code calls llm_query
        Driver->>Interp: {"tool_call": {"name": "llm_query", "args": [...]}}
        Interp->>Interp: _check_and_increment_llm_calls()
        Interp->>Interp: Execute LLM call
        Interp->>Driver: {"tool_result": ...}
    end

    alt Code sets Final variable
        Driver->>Driver: Detect Final in globals
        Driver-->>Interp: {"final": Final_value}
    else Code calls SUBMIT
        Driver-->>Interp: {"final": submit_value}
    end

    Interp-->>Tool: ExecutionResult(stdout, stderr, final)

    opt Volume persistence enabled
        Tool->>VolOps: volume.commit()
        VolOps->>Volume: Commit changes
    end
```

**JSON Protocol:**

The driver communicates via JSON over stdin/stdout:

**Input command:**

```json
{
  "code": "result = analyze_data(df)\nFinal = result",
  "variables": { "df": {} },
  "tool_names": ["llm_query"],
  "output_names": ["result"],
  "execution_profile": "ROOT_INTERLOCUTOR"
}
```

**Output:**

```json
{
  "stdout": "...",
  "stderr": "",
  "final": { "result": {} }
}
```

**Execution Profiles:**

| Profile             | When Used            | Tool Exposure                       |
| ------------------- | -------------------- | ----------------------------------- |
| `ROOT_INTERLOCUTOR` | Primary user chat    | Full tools + sandbox helpers        |
| `RLM_ROOT`          | RLM query mode       | Full tools + sandbox helpers        |
| `RLM_DELEGATE`      | Child RLM delegation | Restricted tools, bounded execution |
| `MAINTENANCE`       | Administrative tasks | Minimal tools                       |

**Key Components:**

| Component                | Source File                     | Role                                                             |
| ------------------------ | ------------------------------- | ---------------------------------------------------------------- |
| `ModalInterpreter`       | `runtime/execution/interpreter.py` | Main interpreter class, manages sandbox lifecycle             |
| `sandbox_driver`         | `runtime/execution/core_driver.py` | Long-lived JSON protocol driver inside sandbox                |
| `VolumeOpsMixin`         | `runtime/tools/modal_volumes.py`   | Volume persistence operations (upload, commit, reload)        |
| `ExecutionProfile`       | `runtime/execution/profiles.py`    | Enum controlling sandbox helper/tool exposure                 |
| `inject_sandbox_helpers` | `runtime/execution/driver_factories.py` | Inject `SUBMIT`, `Final`, `llm_query`, etc. into sandbox globals |

## API and Streaming Surfaces

- **REST contract source**: `openapi.yaml`
- **WebSocket chat stream**: `/api/v1/ws/chat`
- **WebSocket execution stream**: `/api/v1/ws/execution`

Execution stream events are additive observability and do not replace chat envelopes.

## Configuration

Configuration is managed via Hydra with YAML files in `src/fleet_rlm/integrations/config/`:

- `config.yaml`: Base configuration
- Environment overrides via `key=value` CLI arguments

Key configuration areas:

- `interpreter`: Modal interpreter settings (volume, secrets, timeout)
- `agent`: ReAct agent settings (max iterations, delegate LM)
- `server`: FastAPI server settings (host, port, auth mode)
