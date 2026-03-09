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

## Data Flow

### Chat Turn Flow

```mermaid
sequenceDiagram
    participant User
    participant WS as WebSocket
    participant Runtime as chat_runtime.py
    participant Agent as RLMReActChatAgent
    participant Tools as Tools
    participant Sandbox as Modal Sandbox
    participant DB as Neon Postgres

    User->>WS: Send message
    WS->>Runtime: Forward to chat_runtime
    Runtime->>Agent: Process with RLMReActChatAgent
    Agent->>Agent: ReAct loop (think → act → observe)
    Agent->>Tools: Execute tool calls
    Tools->>Sandbox: Run code in Modal
    Sandbox-->>Tools: Return results
    Tools-->>Agent: Tool results
    Agent-->>Runtime: Stream trajectory events
    Runtime-->>WS: Stream events to client
    WS-->>User: Display response
    Agent->>DB: Persist conversation state
```

### RLM Delegation Flow

```mermaid
sequenceDiagram
    participant Parent as Parent Agent
    participant Delegate as delegate_sub_agent.py
    participant Child as Child dspy.RLM
    participant MLflow as MLflow

    Parent->>Delegate: Spawn delegate with task
    Delegate->>Child: Create child RLM instance
    Child->>Child: Recursive reasoning loop
    Child-->>Delegate: Return result
    Delegate->>MLflow: Log trace
    Delegate-->>Parent: Aggregated result
```

### Sandbox Execution Flow

```mermaid
sequenceDiagram
    participant Agent as Agent/Tool
    participant Interpreter as ModalInterpreter
    participant Driver as sandbox_driver
    participant Modal as Modal Cloud
    participant Volume as Modal Volume

    Agent->>Interpreter: Execute code
    Interpreter->>Driver: Get sandbox driver
    Driver->>Modal: Create/lookup sandbox
    Modal-->>Driver: Sandbox instance
    Driver->>Modal: Execute Python code
    Modal-->>Driver: Execution result
    Driver->>Volume: Persist outputs (optional)
    Driver-->>Interpreter: Return result
    Interpreter-->>Agent: Execution result
```

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
