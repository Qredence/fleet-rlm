# System Architecture

Visualizing the structural components and relationships within `fleet-rlm`.

## Overview

```
                        ┌─────────────────────────────────────────┐
                        │           Entry Points                  │
                        │                                         │
                        │  CLI   FastAPI  Ink TUI  MCP   Web UI   │
                        │ (Typer)(WS/REST)(bridge)(stdio)(React)  │
                        └────────────┬────────────────────────────┘
                                     │
                        ┌────────────▼────────────────────────────┐
                        │     RLMReActChatAgent (dspy.Module)     │
                        │                                         │
                        │  ReAct Loop ◄── Chat History            │
                        │      │      ◄── Core Memory             │
                        │      │      ◄── Document Cache          │
                        │      │          (Guardrails)            │
                        │      ▼                                  │
                        │  ┌──────────┬──────────┬────────────┐   │
                        │  │ load_doc │ rlm_query│execute_code│   │
                        │  │ read_file│ llm_query│ edit_file  │   │
                        │  │ chunk_*  │(recursive)│ search    │   │
                        │  └────┬─────┴─────┬────┴─────┬──────┘   │
                        └───────┼───────────┼──────────┼──────────┘
                                │           │          │
                        ┌───────▼───────────▼──────────▼──────────┐
                        │         ModalInterpreter                │
                        │    (JSON protocol · exec profiles)      │
                        │   ROOT │ DELEGATE │ MAINTENANCE         │
                        └────────────────┬────────────────────────┘
                                         │ stdin/stdout
                    ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┼ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                      Modal Cloud        │
                        ┌────────────────▼────────────────────────┐
                        │          Sandbox Driver                 │
                        │   exec() · helpers · tool_call bridge   │
                        │                                         │
                        │   ┌──────────────────────────────────┐  │
                        │   │    Persistent Volume (/data/)    │  │
                        │   │  workspaces/  artifacts/  memory/│  │
                        │   └──────────────────────────────────┘  │
                        └─────────────────────────────────────────┘
```

**Layers at a glance:**

| Layer         | Components                          | Responsibility                                        |
| ------------- | ----------------------------------- | ----------------------------------------------------- |
| Entry Points  | CLI, FastAPI, Ink TUI, MCP, Web UI  | User-facing surfaces — all converge on the same agent |
| Orchestration | `RLMReActChatAgent` + ReAct tools   | DSPy reasoning loop, tool dispatch, history & memory  |
| Execution     | `ModalInterpreter`                  | JSON protocol to sandbox, execution profile gating    |
| Sandbox       | Driver + Volume                     | Isolated Python exec, persistent `/data/` storage     |

## 1. Module Hierarchy

The DSPy module structure of the interactive agent.

```mermaid
graph TD
    Agent[RLMReActChatAgent] -->|wraps| ReAct[dspy.ReAct]
    ReAct -->|uses| Signature[RLMReActChatSignature]
    ReAct -->|calls| Tools[Tool List]

    subgraph "Tools"
        Tools -->|Standard| FS[File System Tools]
        Tools -->|Delegate| RLM[dspy.RLM Wrappers]
        Tools -->|Sandbox| Edit[Edit File / Chunking]
    end

    RLM -->|"uses"| Interpreter[ModalInterpreter]
    Edit -->|uses| Interpreter
```

## 2. Component Architecture (Web UI & Backend)

Top-level system components and data flow relative to the user and the local backend.

```mermaid
graph LR
    User([User]) <-->|Browser| React[Vite / React Web UI]
    React <-->|WebSocket /api/v1/ws/chat| FastAPI[FastAPI Backend]
    React <-->|HTTP REST /api/v1/*| FastAPI

    FastAPI <-->|SQLite| DB[(fleet_rlm.db)]
    FastAPI <-->|Stream| Agent[RLMReActChatAgent]

    Agent <-->|Context| History[dspy.History]
    Agent <-->|LLM Calls| Model[Language Model]

    subgraph "Execution Plane"
        Agent <-->|JSON Protocol| Sandbox[Modal Sandbox]
        Sandbox <-->|Read/Write| Volume[Modal Volume]
        Sandbox <-->|Exec| Python[Python Runtime]
    end
```

## 3. Deployment Topology

Physical/Network view of the deployment. `fleet-rlm` runs locally, serving a built React frontend via FastAPI, and executes computational workloads in Modal.

```mermaid
graph TD
    Client[Browser] -->|HTTP / WS| App[FastAPI App (Local)]

    subgraph "Local Environment"
        App -->|Serves Static Files| Dist[Frontend Dist]
        App -->|Reads/Writes| DB[(SQLite DB)]
        App -->|Host| Agent[RLMAgent Instance]
    end

    subgraph "Modal Cloud"
        Agent -->|gRPC/HTTP| ModalAPI[Modal API]
        ModalAPI -->|Spawns| Container[Sandbox Container]
    end

    subgraph "LLM Providers"
        Agent -->|API| OpenAI[OpenAI / Anthropic / Gemini]
    end
```

## 4. RLM Recursive Structure

How `dspy.RLM` handles complex tasks through recursion.

```mermaid
graph TD
    Root[Root Agent] -->|Call| RLM_A[RLM: Planner]
    RLM_A -->|Spawns| RLM_B1[RLM: Worker 1]
    RLM_A -->|Spawns| RLM_B2[RLM: Worker 2]

    RLM_B1 -->|Exec| Sandbox1[Sandbox 1]
    RLM_B2 -->|Exec| Sandbox2[Sandbox 2]

    Sandbox1 -->|Result| RLM_B1
    Sandbox2 -->|Result| RLM_B2

    RLM_B1 -->|Report| RLM_A
    RLM_B2 -->|Report| RLM_A

    RLM_A -->|Final Answer| Root
```
