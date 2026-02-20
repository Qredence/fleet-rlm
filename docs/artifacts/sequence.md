# Chronological AI Sequence Diagram

This artifact models the end-to-end event sequence when a user queries the `fleet-rlm` agentic architecture. This specifically details the interaction between the primary ReAct Supervisor and the subordinate RLM Worker, as they concurrently multiplex feedback to the React UI.

## Execution Flow: "Read my data.csv and calculate the average revenue."

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as React UI
    participant WS as FastAPI WS Router
    participant Sup as DSPy ReAct Supervisor
    participant Neon as Neon DB (Memory)
    participant Dec as DSPy RLM Decomposer
    participant Work as DSPy RLM Worker
    participant Modal as Modal Workspace (Filesystem)

    %% Initial Query & Thought
    User->>UI: "Read data.csv and calculate average revenue."
    UI->>WS: WebSocket JSON { type: "chat", payload: { ... } }
    WS->>Sup: Route to Supervisor
    activate Sup
    Sup-->>WS: yield { type: "chat", content: "Thinking: How do I calculate this..." }
    WS-->>UI: WebSocket Stream [chat text updates]

    %% Evolutive Memory Context Lookups
    Sup->>Neon: search_evolutive_memory("data.csv revenue")
    activate Neon
    Neon-->>Sup: Returns previous TaxonomyNodes and Observations
    deactivate Neon
    Sup-->>WS: yield { type: "chat", content: "Found context about data.csv formatting..." }

    %% Delagating the Execution
    Sup->>Dec: RLMEngine Delegated (Task: Calculate revenue data.csv)
    activate Dec
    Sup-->>WS: yield { type: "chat", content: "Calling RLM Engine for execution..." }

    %% RLM Decomposition
    Dec-->>WS: yield { type: "plan_update", step: 1, text: "Read CSV file" }
    Dec-->>WS: yield { type: "plan_update", step: 2, text: "Extract 'revenue' column and average" }
    WS-->>UI: UI displays Decomposition Plan in Side Panel
    Dec->>Work: Delegate Step 1 & 2 Execution
    deactivate Dec

    %% RLM Python Worker Loop
    activate Work
    Work-->>WS: yield { type: "rlm_executing", status: "Writing Python Script..." }
    Work->>Modal: execute_workspace_code(code="import pandas...")
    activate Modal
    Modal-->>Work: stdout: "Average revenue is $1250.00"
    deactivate Modal
    Work-->>WS: yield { type: "rlm_executing", status: "Execution complete." }
    WS-->>UI: UI Mock Terminal renders `execute_chunk` output
    Work-->>Sup: Returns Result: "Average revenue is $1250.00"
    deactivate Work

    %% Final Summary back to User
    Sup-->>WS: yield { type: "chat", content: "The RLM Engine calculated the average revenue as $1250.00." }
    WS-->>UI: Displays final answer in Chat UI
    deactivate Sup
    UI-->>User: Views the final result and the visual plan history
```
