# System Architecture

Visualizing the structural components and relationships within `fleet-rlm`.

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

## 2. Component Architecture

Top-level system components and data flow relative to the user and cloud infrastructure.

```mermaid
graph LR
    User([User]) <-->|WebSocket| Server[Litellm Proxy / API]
    Server <-->|Stream| Agent[RLMReActChatAgent]

    Agent <-->|Context| History[dspy.History]
    Agent <-->|LLM Calls| Model[Language Model]

    subgraph "Execution Plane"
        Agent <-->|JSON Protocol| Sandbox[Modal Sandbox]
        Sandbox <-->|Read/Write| Volume[Modal Volume]
        Sandbox <-->|Exec| Python[Python Runtime]
    end
```

## 3. Network Topology

Physical/Network view of the deployment.

```mermaid
graph TD
    Client[Client App] -->|HTTPS/WSS| LB[Load Balancer]
    LB -->|Traffic| Service[Cloud Run Service]

    subgraph "Cloud Run"
        Service -->|Runs| App[FastAPI App]
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
