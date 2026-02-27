# User Interaction Flows

Visualizing the key sequence of events during user interactions with `fleet-rlm`.

## 1. Standard Chat Turn

Simple question and answer flow without tools.

```mermaid
sequenceDiagram
    actor User
    participant WebSocket
    participant Agent
    participant LLM

    User->>WebSocket: "Hello, who are you?"
    WebSocket->>Agent: Receive Message
    Agent->>LLM: Prompt (History + User Request)
    LLM-->>Agent: "I am fleet-rlm, an AI agent..."
    Agent->>WebSocket: Stream Response
    WebSocket-->>User: "I am fleet-rlm, an AI agent..."
```

## 2. Tool-Assisted Chat Turn

User asks for information requiring a tool (e.g., read a file).

```mermaid
sequenceDiagram
    actor User
    participant WebSocket
    participant Agent
    participant Tools
    participant LLM

    User->>WebSocket: "What's in README.md?"
    WebSocket->>Agent: Receive Message
    Agent->>LLM: Prompt (History + Request)
    LLM-->>Agent: Thought: "I need to read README.md"
    LLM-->>Agent: Action: load_document("README.md")

    Agent->>Tools: load_document("README.md")
    Tools->>Tools: Read File Content
    Tools-->>Agent: "File loaded successfully. Content: # Project..."

    Agent->>LLM: Prompt (Observation: File Content)
    LLM-->>Agent: Thought: "I have the content now."
    LLM-->>Agent: "The README says this project is..."

    Agent->>WebSocket: Stream Response
    WebSocket-->>User: "The README says this project is..."
```

## 3. RLM Delegation Flow

User requests complex analysis requiring the Recursive Language Model.

```mermaid
sequenceDiagram
    actor User
    participant Agent
    participant RLM
    participant Sandbox
    participant LLM

    User->>Agent: "Analyze the deployment logs for errors."
    Agent->>LLM: Prompt
    LLM-->>Agent: Action: extract_from_logs()

    rect rgb(240, 240, 240)
        note right of Agent: Delegation to RLM
        Agent->>RLM: Start RLM Pipeline

        loop RLM Reasoning
            RLM->>LLM: "How do I extract errors?"
            LLM-->>RLM: Code: "grep 'ERROR' logs.txt"
            RLM->>Sandbox: Execute Code
            Sandbox-->>RLM: "Found 5 errors..."
            RLM->>RLM: Refine / Iterate
        end

        RLM-->>Agent: Structured Result (JSON)
    end

    Agent->>LLM: Prompt (Observation: Analysis Result)
    LLM-->>Agent: "I found 5 critical errors..."
    Agent->>User: Final Response
```

## 4. Sandbox Code Editing

User asks to modify a file, triggering sandbox interaction.

```mermaid
sequenceDiagram
    actor User
    participant Agent
    participant Sandbox
    participant FileSystem

    User->>Agent: "Change 'port=80' to 'port=8080' in config.py"
    Agent->>Sandbox: edit_file("config.py", "port=80", "port=8080")

    note right of Sandbox: Robust Edit Logic
    Sandbox->>FileSystem: Read config.py
    Sandbox->>Sandbox: Verify snippet uniqueness
    Sandbox->>FileSystem: Write updated content

    Sandbox-->>Agent: "Success: File updated."
    Agent-->>User: "I've updated the port to 8080."
```
