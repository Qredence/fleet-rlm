# System Component Diagram (Surgical Integration)

This artifact maps the master architecture of the `fleet-rlm` project, visualizing how the separate client, backend, and infrastructure layers communicate through the DSPy components using the new Surgical integration method.

## Macro Architecture

```mermaid
graph TD
    %% -- Clients --
    subgraph Frontend [Client Interfaces]
        Vite[Vite + React Dashboard Store]
        TUI[Terminal UI CLI]
    end

    %% -- Application Backend --
    subgraph Backend [FastAPI Application Shell]
        Router[Multiplexed WebSocket Router]
        REST[REST API Endpoints]
    end

    %% -- Agentic Intelligence (Surgically Upgraded) --
    subgraph AICore [DSPy AI Core]
        Sup(RLMReActChatAgent - Supervisor)
        RLM(RLMEngine - Injected into sandbox.py)
        LiteLLM{LiteLLM Proxy}
        Sup --> |Routes via StatefulSandboxManager| RLM
        RLM --> |LLM Inference| LiteLLM
        Sup --> |LLM Inference| LiteLLM
    end

    %% -- External Infrastructure --
    subgraph Infrastructure [Data & Cloud Infrastructure]
        Modal[(Modal Persisted Workspace)]
        Neon[(Neon Postgres DB + pgvector)]
        PostHog((PostHog Tracing))
    end

    %% Boundary Edges
    Vite -.-> |Multiplexed JSON Stream ws://| Router
    TUI -.-> |Multiplexed JSON Stream ws://| Router

    Router --> |Async payload callbacks| Sup

    RLM --> |Python Code Execution execute_chunk| Modal
    Modal --> |Output & Context Guard <2000 chars| RLM

    Sup --> |search_evolutive_memory tool| Neon
    RLM --> |search_evolutive_memory tool| Neon
    Neon --> |SQLModel TaxonomyNodes| Sup

    LiteLLM --> |API telemetry context| PostHog
    AICore -.- |Generates DSPy traces| PostHog
```
