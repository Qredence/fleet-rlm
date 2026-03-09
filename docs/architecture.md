# Architecture Overview

This document describes the maintained architecture for `fleet-rlm`, a Recursive Language Model system built on DSPy and Modal.

## System Architecture Diagram

The following diagram shows the complete system architecture with all major components and their relationships:

```mermaid
graph TB
    subgraph ENTRY["Entry Points"]
        CLI_FLEET["fleet CLI<br/>(fleet_cli.py)"]
        CLI_RLM["fleet-rlm CLI<br/>(cli.py)"]
        WEB_UI["Web UI<br/>(frontend/)"]
        MCP_CLIENT["MCP Client<br/>(mcp/server.py)"]
    end

    subgraph SERVER["Service Layer (server/)"]
        MAIN["FastAPI App<br/>(main.py)"]
        ROUTERS["Routers<br/>(routers/)"]
        AUTH["Auth<br/>(auth/)"]
        WS["WebSocket<br/>(routers/ws/)"]
    end

    subgraph ORCHESTRATION["Orchestration Layer (react/)"]
        AGENT["RLMReActChatAgent<br/>(agent.py)"]
        SIGNATURES["Signatures<br/>(signatures.py)"]
        STREAMING["Streaming<br/>(streaming.py, streaming_context.py)"]
        DELEGATE["Delegate Sub-Agent<br/>(delegate_sub_agent.py)"]
    end

    subgraph TOOLS["Tools Layer (react/tools/)"]
        TOOL_DELEGATE["delegate.py"]
        TOOL_DOCUMENT["document.py"]
        TOOL_SANDBOX["sandbox.py"]
        TOOL_MEMORY["memory_intelligence.py"]
        TOOL_CHUNKING["chunking.py"]
        TOOL_FILESYSTEM["filesystem.py"]
    end

    subgraph EXECUTION["Execution Layer (core/)"]
        INTERPRETER["ModalInterpreter<br/>(interpreter.py)"]
        DRIVER["Sandbox Driver<br/>(driver.py)"]
        LLM_TOOLS["LLM Tools<br/>(llm_tools.py)"]
        VOLUME_OPS["Volume Operations<br/>(volume_ops.py)"]
        SANDBOX_TOOLS["Sandbox Tools<br/>(sandbox_tools.py)"]
    end

    subgraph MODAL["Modal Cloud"]
        SANDBOX["Modal Sandbox"]
        VOLUME["Modal Volume"]
    end

    subgraph PERSISTENCE["Persistence Layer"]
        DB["Database (db/)<br/>engine.py, models.py, repository.py"]
        NEON[("Neon Postgres<br/>(RLS-enabled)")]
        MLFLOW["MLflow Integration<br/>(analytics/)"]
        POSTHOG["PostHog Callback<br/>(analytics/)"]
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
    SANDBOX_TOOLS --> SANDBOX

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
    class TOOL_DELEGATE,TOOL_DOCUMENT,TOOL_SANDBOX,TOOL_MEMORY,TOOL_CHUNKING,TOOL_FILESYSTEM toolsNode
    class INTERPRETER,DRIVER,LLM_TOOLS,VOLUME_OPS,SANDBOX_TOOLS executionNode
    class SANDBOX,VOLUME modalNode
    class DB,NEON,MLFLOW,POSTHOG persistenceNode
    class PLANNER_LM,DELEGATE_LM llmNode
```

## Entry Points

| Entry Point | Source File | Description |
|-------------|-------------|-------------|
| `fleet` | `src/fleet_rlm/fleet_cli.py` | Primary interactive chat launcher. Supports `fleet web` subcommand for Web UI. |
| `fleet-rlm` | `src/fleet_rlm/cli.py` | Full CLI with `chat`, `serve-api`, `serve-mcp`, `init` commands. |
| Web UI | `src/frontend/` | React/TypeScript frontend served by FastAPI at `http://0.0.0.0:8000`. |
| MCP Server | `src/fleet_rlm/mcp/server.py` | Model Context Protocol server for Claude Desktop integration. |

## Core Layers

### 1. Entry Points Layer

Entry points define how users interact with the system:

- **`fleet_cli.py`**: Lightweight wrapper that provides `fleet` command for terminal chat and `fleet web` for Web UI launch.
- **`cli.py`**: Full Typer-based CLI with subcommands:
  - `chat`: Standalone interactive terminal chat
  - `serve-api`: FastAPI server for HTTP/WebSocket API
  - `serve-mcp`: MCP server for Claude Desktop integration
  - `init`: Bootstrap Claude Code scaffold assets

### 2. Orchestration Layer (`react/`)

The orchestration layer manages the ReAct agent loop and streaming:

| Module | Purpose |
|--------|---------|
| `agent.py` | `RLMReActChatAgent` - stateful conversational agent with tool use |
| `signatures.py` | DSPy signature definitions for agent inputs/outputs |
| `streaming.py` | Real-time streaming of chat turns and trajectory events |
| `streaming_context.py` | Context management for streaming sessions |
| `delegate_sub_agent.py` | Spawns child `dspy.RLM` instances for recursive reasoning |
| `commands.py` | Built-in command dispatch (e.g., `/help`, `/reset`) |

### 3. Tools Layer (`react/tools/`)

Tools provide capabilities for the ReAct agent:

| Module | Purpose |
|--------|---------|
| `delegate.py` | Delegates tasks to child RLM agents |
| `document.py` | Document loading and processing |
| `sandbox.py` | Code execution in Modal sandbox |
| `memory_intelligence.py` | Intelligent memory management |
| `chunking.py` | Text chunking for long documents |
| `filesystem.py` | File system operations in sandbox |

### 4. Execution Layer (`core/`)

The execution layer handles remote code execution in Modal:

| Module | Purpose |
|--------|---------|
| `interpreter.py` | `ModalInterpreter` - manages Modal sandbox lifecycle |
| `driver.py` | `sandbox_driver` - executes Python code in sandbox |
| `driver_factories.py` | Factory functions for driver configuration |
| `llm_tools.py` | LLM-backed tools for the sandbox |
| `sandbox_tools.py` | Helper tools for sandbox operations |
| `volume_ops.py` | Modal volume operations for persistence |
| `volume_tools.py` | Tools for volume management |

### 5. Service Layer (`server/`)

The service layer provides HTTP and WebSocket APIs:

| Module | Purpose |
|--------|---------|
| `main.py` | FastAPI application factory |
| `routers/runtime.py` | Runtime settings and status endpoints |
| `routers/sessions.py` | Session state management |
| `routers/traces.py` | MLflow trace endpoints |
| `routers/health.py` | Health check endpoints (`/health`, `/ready`) |
| `routers/auth.py` | Authentication endpoints |
| `routers/ws/chat_runtime.py` | WebSocket chat runtime |
| `routers/ws/api.py` | WebSocket API surface |
| `auth/` | Authentication middleware (dev/Entra modes) |

### 6. Persistence Layer

| Component | Purpose |
|-----------|---------|
| `db/engine.py` | Async database engine with connection pooling |
| `db/models.py` | SQLModel definitions for runs, steps, artifacts, memory |
| `db/repository.py` | Repository pattern for database operations |
| `analytics/mlflow_integration.py` | MLflow tracing for DSPy optimization |
| `analytics/posthog_callback.py` | PostHog telemetry callback |

## Data Flow Diagrams

### Chat Turn Flow

This diagram shows the complete flow of a chat turn from user message to response streaming, based on the WebSocket runtime in `src/fleet_rlm/server/routers/ws/`.

```mermaid
sequenceDiagram
    autonumber
    participant User as User
    participant WS as WebSocket<br/>(ws/api.py)
    participant MsgLoop as message_loop.py
    participant Runtime as chat_runtime.py
    participant Agent as RLMReActChatAgent<br/>(agent.py)
    participant StreamCtx as StreamingContext<br/>(streaming_context.py)
    participant Stream as streaming.py
    participant Tools as Tools<br/>(react/tools/)
    participant Interpreter as ModalInterpreter<br/>(interpreter.py)
    participant Modal as Modal Sandbox
    participant DB as Neon Postgres<br/>(db/repository.py)

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

| Component | Source File | Role |
|-----------|-------------|------|
| `parse_ws_message_or_send_error` | `ws/message_loop.py` | Parse incoming WebSocket JSON into `WSMessage` |
| `_prepare_chat_runtime` | `ws/chat_runtime.py` | Initialize agent with planner LM, delegate LM, repository |
| `StreamingContext` | `react/streaming_context.py` | Immutable snapshot of agent state for event enrichment |
| `aiter_chat_turn_stream` | `react/streaming.py` | Async iterator yielding `StreamEvent` objects |

### RLM Delegation Flow

This diagram shows how parent agents spawn child RLM instances for recursive reasoning, based on `src/fleet_rlm/react/delegate_sub_agent.py`.

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

| Function | Source File | Purpose |
|----------|-------------|---------|
| `spawn_delegate_sub_agent_async` | `react/delegate_sub_agent.py` | Main entry point for delegation |
| `_claim_delegate_slot_or_error` | `react/delegate_sub_agent.py` | Enforce `delegate_max_calls_per_turn` limit |
| `_build_child_interpreter` | `react/delegate_sub_agent.py` | Create or reuse ModalInterpreter for child |
| `build_recursive_subquery_rlm` | `react/rlm_runtime_modules.py` | Construct `dspy.RLM` module with sandbox tools |
| `_delegate_streaming_context` | `react/delegate_sub_agent.py` | Build `StreamingContext` for child depth tracking |

**Depth and Budget Controls:**
- `max_depth`: Maximum recursion depth (default: 2)
- `delegate_max_calls_per_turn`: Maximum delegate calls per parent turn (default: 8)
- `max_llm_calls`: LLM call budget shared between parent and children

### Sandbox Execution Flow

This diagram shows how code execution flows from tool calls through the ModalInterpreter to the sandbox driver, based on `src/fleet_rlm/core/interpreter.py` and `driver.py`.

```mermaid
sequenceDiagram
    autonumber
    participant Tool as Tool<br/>(react/tools/)
    participant Interp as ModalInterpreter<br/>(interpreter.py)
    participant VolOps as VolumeOpsMixin<br/>(volume_ops.py)
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

```json
// Input command
{
  "code": "result = analyze_data(df)\nFinal = result",
  "variables": {"df": {...}},
  "tool_names": ["llm_query"],
  "output_names": ["result"],
  "execution_profile": "ROOT_INTERLOCUTOR"
}

// Output
{
  "stdout": "...",
  "stderr": "",
  "final": {"result": {...}}
}
```

**Execution Profiles:**

| Profile | When Used | Tool Exposure |
|---------|-----------|---------------|
| `ROOT_INTERLOCUTOR` | Primary user chat | Full tools + sandbox helpers |
| `RLM_ROOT` | RLM query mode | Full tools + sandbox helpers |
| `RLM_DELEGATE` | Child RLM delegation | Restricted tools, bounded execution |
| `MAINTENANCE` | Administrative tasks | Minimal tools |

**Key Components:**

| Component | Source File | Role |
|-----------|-------------|------|
| `ModalInterpreter` | `core/interpreter.py` | Main interpreter class, manages sandbox lifecycle |
| `sandbox_driver` | `core/driver.py` | Long-lived JSON protocol driver inside sandbox |
| `VolumeOpsMixin` | `core/volume_ops.py` | Volume persistence operations (upload, commit, reload) |
| `ExecutionProfile` | `core/interpreter.py` | Enum controlling sandbox helper/tool exposure |
| `inject_sandbox_helpers` | `core/driver_factories.py` | Inject `SUBMIT`, `Final`, `llm_query`, etc. into sandbox globals |

## API and Streaming Surfaces

- **REST contract source**: `openapi.yaml`
- **WebSocket chat stream**: `/api/v1/ws/chat`
- **WebSocket execution stream**: `/api/v1/ws/execution`

Execution stream events are additive observability and do not replace chat envelopes.

## Configuration

Configuration is managed via Hydra with YAML files in `src/fleet_rlm/conf/`:

- `config.yaml`: Base configuration
- Environment overrides via `key=value` CLI arguments

Key configuration areas:
- `interpreter`: Modal interpreter settings (volume, secrets, timeout)
- `agent`: ReAct agent settings (max iterations, delegate LM)
- `server`: FastAPI server settings (host, port, auth mode)
