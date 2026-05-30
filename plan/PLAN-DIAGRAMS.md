Here are the key interaction diagrams for the planned `fleet‑rlm` system. Use any Mermaid renderer (e.g., GitHub, Mermaid Live) to view them.

---

## 1. High‑Level Architecture

```mermaid
graph TB
    subgraph Frontend
        ChatUI[Chat + Canvas]
    end
    subgraph Backend
        API[FastAPI Backend]
        Semaphore[Async Semaphore 5]
    end
    subgraph Daytona_Cloud
        Vol[Persistent Volume<br/>/data]
        RootSB[Root Sandbox<br/>session-scoped]
        ChildSB1[Child Sandbox 1<br/>subpath isolated]
        ChildSB2[Child Sandbox 2<br/>subpath isolated]
    end
    subgraph Inside_Sandbox
        Agent[DSPy Agent<br/>ChainOfThought + RLM]
        Tools[Tools: web_search,<br/>remember, recall,<br/>delegate, etc.]
        VolMount[Volume Mount /data]
    end
    MLflow[MLflow Tracking Server]
    LLM[LLM Providers<br/>OpenRouter]

    ChatUI <-->|WebSocket| API
    API <-->|Daytona SDK| RootSB
    API -.-> Semaphore
    RootSB --> Vol
    ChildSB1 -->|subpath mount| Vol
    ChildSB2 -->|subpath mount| Vol
    RootSB --> Agent
    Agent --> Tools
    Agent -->|LLM calls| LLM
    Agent -.->|tracking| MLflow
```

---

## 2. Scenario A: Simple Chat (Warm Sandbox)

User asks a quick question; sandbox already running.

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Frontend
    participant BE as Backend (FastAPI)
    participant SB as Root Sandbox (running)
    participant Agent as DSPy Agent
    participant Mem as Memory DB /data

    U->>FE: "What is the capital of France?"
    FE->>BE: WebSocket send message
    BE->>SB: Execute agent with message
    SB->>Agent: forward("What is the capital of France?")
    Agent->>Agent: ChainOfThought: no tools needed
    Agent->>Mem: recall("capital of France") (optional)
    Mem-->>Agent: (empty or known fact)
    Agent->>LLM: One lightweight completion
    LLM-->>Agent: "The capital of France is Paris."
    Agent->>Agent: remember("capital of France", "Paris") (optional)
    Agent-->>SB: response
    SB-->>BE: response
    BE->>FE: WebSocket response
    FE->>U: Display "The capital of France is Paris."
```

---

## 3. Scenario B: Complex Task with Child Delegation

User asks to investigate TODOs in a codebase; agent escalates to RLM and spawns children.

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Frontend
    participant BE as Backend
    participant RootSB as Root Sandbox
    participant Agent as DSPy Agent (RLM)
    participant Vol as Persistent Volume
    participant ChildSB as Child Sandbox (ephemeral)

    U->>FE: "Find all TODO comments in sklearn and fix the easiest one"
    FE->>BE: WebSocket send
    BE->>RootSB: Execute agent with task
    RootSB->>Agent: forward(task)
    Agent->>Agent: ChainOfThought detects [TOOLS NEEDED]
    Agent->>Agent: RLM loop starts (max_iters=60)

    loop RLM Iterations
        Agent->>Tools: execute("ls sklearn/") etc.
        Agent->>Agent: Decides to delegate
        Agent->>Tools: delegate_to_rlm_batched([
            "TODOs in sklearn/linear_model/",
            "TODOs in sklearn/ensemble/",
            "TODOs in sklearn/tree/"
        ])

        par Parallel child creation
            Tools->>BE: Spawn Child Sandbox (linear_model)
            Tools->>BE: Spawn Child Sandbox (ensemble)
            Tools->>BE: Spawn Child Sandbox (tree)
        end

        BE->>ChildSB: Create sandbox with VolumeMount(subpath=sessions/child1)
        BE->>ChildSB: Create sandbox with VolumeMount(subpath=sessions/child2)
        BE->>ChildSB: Create sandbox with VolumeMount(subpath=sessions/child3)

        ChildSB->>ChildSB: Child RLM runs, searches code
        ChildSB-->>RootSB: Return result ("Found 5 TODOs, easiest is ...")
    end

    Agent->>Tools: edit_file(...) to fix the easiest TODO
    Agent->>Tools: SUBMIT("Patch generated")
    Agent-->>RootSB: Final answer + patch
    RootSB-->>BE: result
    BE->>FE: WebSocket response with patch preview
    FE->>U: Show fixed file diff
```

---

## 4. Scenario C: Session Resumption After Auto‑Stop

User returns after a long break; the previous sandbox has been stopped by Daytona. A new sandbox is created with the same volume, restoring state.

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Frontend
    participant BE as Backend
    participant DaytonaAPI as Daytona API
    participant Vol as Persistent Volume
    participant NewSB as New Root Sandbox
    participant Agent as DSPy Agent

    Note over BE: Session idle > 15 min<br/>Previous sandbox auto-stopped
    U->>FE: Open chat, send "What did we decide about the API rate limit?"
    FE->>BE: Reconnect with session_id
    BE->>DaytonaAPI: Get volume "fleet-rlm-brain"
    DaytonaAPI-->>BE: volume id
    BE->>DaytonaAPI: Create new sandbox with volume mount
    DaytonaAPI-->>BE: new sandbox id
    BE->>NewSB: Start agent server
    NewSB->>Vol: Mount /data
    NewSB->>Agent: restore state from /data/sessions/<id>/conversation.json
    Agent->>Agent: Generate conversation summary from stored history
    Agent->>Agent: recall("API rate limit") -> "decided: 100 req/min"
    BE->>NewSB: forward("What did we decide about the API rate limit?")
    Agent->>Agent: ChainOfThought + memory retrieval
    Agent-->>NewSB: "We agreed on a limit of 100 requests per minute."
    NewSB-->>BE: response
    BE->>FE: WebSocket response
    FE->>U: Display "We agreed on a limit of 100 requests per minute."
```

---

## 5. Concurrency Control (Semaphore)

When the 5‑sandbox cap is reached, new sandbox creation is queued.

```mermaid
sequenceDiagram
    participant BE as Backend
    participant Sema as Semaphore(5)
    participant Daytona as Daytona API

    Note over BE,Sema: 5 sandboxes already running
    BE->>Sema: acquire() for new child
    Sema-->>BE: block until slot available

    par Existing sandbox finishes
        Sema->>Sema: release()
        Sema-->>BE: acquire() succeeds
    end

    BE->>Daytona: create child sandbox
    Daytona-->>BE: sandbox created
```

---

These diagrams capture the core flows of the post‑plan architecture. Would you like any additional scenario or a more detailed component breakdown?