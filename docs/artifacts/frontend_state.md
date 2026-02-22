# Frontend State Flow Diagram (Surgical Integration)

This artifact maps the frontend reactivity paradigm. It illustrates how the React dashboard handles rapid AI execution data (using Zustand for fast, transient state) while retaining persistent structural data (using TanStack Query for robust Neon DB synchronization).

## React UI Data & Event Flow

```mermaid
flowchart TD
    %% Base Network Layer
    Backend[FastAPI / Neon DB]
    WS((WebSocket Listener))

    Backend -.-> |Multiplexed JSON Stream| WS

    %% Zustand Architecture (Fast, streaming transient state)
    subgraph Zustand [Zustand Stores - Fast Streaming State]
        ChatStore[(chatHistory State)]
        RLMStore[(activeRLM State)]
    end

    %% TanStack Query Architecture (Persistent, synced server state)
    subgraph TanStack [TanStack Query - Server Synchronized State]
        QueryClient[(Taxonomy Cache)]
    end

    %% Payload Demultiplexing
    WS --> |"{type: 'chat'}"| ChatStore
    WS --> |"{type: 'plan_update'}"| RLMStore
    WS --> |"{type: 'rlm_executing'}"| RLMStore

    %% Invalidation Trigger
    WS --> |"{type: 'memory_update'}"| Invalidator[Invalidate 'memory' Query]
    Invalidator --> QueryClient
    QueryClient -.-> |Refetch| Backend

    %% React Component Rendering Boundaries
    subgraph UI [React Components]
        LeftPane[Left Chat Pane]
        RightPane[Right Workspace Pane]
        Sidebar[Taxonomy Sidebar]
    end

    %% State to UI mappings
    ChatStore --> |Re-renders| LeftPane
    RLMStore --> |Re-renders| RightPane
    QueryClient --> |Re-renders| Sidebar
```
