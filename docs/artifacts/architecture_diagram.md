# Architecture Diagram: fleet-rlm v0.5 (Hyper-Advanced)

## Full System Topology

```mermaid
graph TB
    subgraph Clients [Client Layer]
        direction LR
        React[Vite + React Frontend]
        CLI[tui-cli Terminal]
        Ink[tui-ink Terminal]
    end

    subgraph Server ["FastAPI Server (server/)"]
        direction TB
        WSRouter[WebSocket Router]
        RESTRouter[REST API Router]
        Auth[Auth Middleware]
        WSRouter -.-> |StreamEvent| Clients
        RESTRouter -.-> |JSON| Clients
    end

    subgraph Supervisor ["ReAct Supervisor (react/)"]
        Agent["RLMReActChatAgent
        (dspy.Module)"]
        CoreMem[CoreMemoryMixin]
        DocCache[DocumentCacheMixin]
        ReAct["dspy.ReAct
        max_iters=10"]
        ToolList["build_tool_list()
        • sandbox tools
        • document tools
        • filesystem tools
        • memory tools
        • chunking tools"]

        Agent --> CoreMem
        Agent --> DocCache
        Agent --> ReAct
        ReAct --> ToolList
    end

    subgraph RLMLayer ["RLM Engine (stateful/)"]
        SSM["StatefulSandboxManager"]
        RLMEngine["RLMEngine
        (dspy.Module)
        max_iters=3"]
        CodeGen["CodeGenerationSignature
        (dspy.ChainOfThought)"]
        Guard["Context Guard
        MAX_CHARS=2000"]

        SSM --> RLMEngine
        RLMEngine --> CodeGen
        RLMEngine --> Guard
    end

    subgraph Interpreter ["Modal Interpreter (core/)"]
        MI["ModalInterpreter
        (CodeInterpreter)"]
        Driver["sandbox_driver()
        JSON Protocol"]
        LLMQuery["llm_query()
        llm_query_batched()"]
        SandboxTools["sandbox_tools.py
        peek/grep/chunk/*"]
        VolumeTools["volume_tools.py
        workspace_*"]
        SessionHist["session_history.py"]

        MI --> Driver
        Driver --> LLMQuery
        Driver --> SandboxTools
        Driver --> VolumeTools
        Driver --> SessionHist
    end

    subgraph Memory ["Evolutive Memory (memory/)"]
        Schema["schema.py
        TaxonomyNode
        AgentMemory"]
        DB["db.py
        asyncpg Engine"]
        MemTools["memory_tools.py
        @dspy.tool"]
    end

    subgraph Infrastructure [Cloud Infrastructure]
        Modal[(Modal Sandbox
        + Volume /data/)]
        Neon[(Neon Postgres
        + pgvector)]
        LiteLLM{LiteLLM Proxy}
        PostHog((PostHog
        Telemetry))
    end

    %% Connections
    Clients --> Server
    Server --> Agent
    Agent --> SSM
    SSM --> MI
    MI --> Modal
    Agent --> MemTools
    MemTools --> DB
    DB --> Neon
    Schema --> DB
    MI --> LiteLLM
    Agent --> LiteLLM
    LiteLLM --> PostHog
```

## Module Dependency Graph

```mermaid
graph LR
    agent.py --> tools.py
    agent.py --> streaming.py
    agent.py --> commands.py
    agent.py --> validation.py
    agent.py --> core_memory.py
    agent.py --> document_cache.py
    agent.py --> tool_delegation.py
    agent.py --> runtime_factory.py
    agent.py --> interpreter.py

    interpreter.py --> driver.py
    interpreter.py --> driver_factories.py
    interpreter.py --> sandbox_tools.py
    interpreter.py --> session_history.py
    interpreter.py --> volume_tools.py
    interpreter.py --> llm_tools.py
    interpreter.py --> output_utils.py
    interpreter.py --> volume_ops.py

    sandbox.py --> interpreter.py

    memory_tools.py --> db.py
    memory_tools.py --> schema.py
```

## Data Flow Layers

| Layer                | Component                   | Protocol               | Data                            |
| :------------------- | :-------------------------- | :--------------------- | :------------------------------ |
| **L1 - Client**      | React / TUI                 | WebSocket JSON         | `StreamEvent` typed payloads    |
| **L2 - API**         | FastAPI                     | HTTP / WS              | REST endpoints + WS stream      |
| **L3 - Supervisor**  | `RLMReActChatAgent`         | DSPy Module call       | `dspy.Prediction`               |
| **L4 - RLM**         | `RLMEngine` in `sandbox.py` | Internal Python        | `SandboxResult`                 |
| **L5 - Interpreter** | `ModalInterpreter`          | JSON over stdin/stdout | Code + tool_call payloads       |
| **L6 - Sandbox**     | Modal Container             | Python `exec()`        | Globals dict + `SUBMIT`/`Final` |
| **L7 - Storage**     | Neon + Modal Volume         | SQL / Filesystem       | Embeddings + workspace files    |
