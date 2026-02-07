flowchart TD
    %% Layers
    User["User Code"]:::external

    subgraph "Layer 1: User API"
        API["API Entry Point\n(dspy/__init__.py)"]:::core
    end

    subgraph "Layer 2: Core Pipeline & Modules"
        Primitives["Primitives\n(Example, History, Tool, Prediction)"]:::core
        Modules["Modules\n(ChainOfThought, Parallel, Program, ReAct, Refine)"]:::core
        PipelineMgr["Pipeline Manager\n(orchestration & retry)"]:::core
    end

    subgraph "Layer 3: Subsystems"
        Adapters["Adapters\n(ChatAdapter, JSONAdapter, TwoStepAdapter)"]:::subsystem
        Types["Adapter Type Definitions"]:::subsystem
        Clients["LM Client Abstractions"]:::subsystem
        Retrieval["Retrieval Subsystem\n(FAISS, Pinecone, Chroma)"]:::subsystem
        Teleprompt["Teleprompt Optimizers"]:::subsystem
        Evaluation["Evaluation & Metrics"]:::utilOrange
        Experimental["Experimental Features"]:::subsystem
        Utilities["Utilities\n(caching, logging, asyncify, etc.)"]:::utilOrange
    end

    subgraph "Layer 4: External Services"
        LMAPI["LM APIs"]:::external
        VectorDB["Vector DBs"]:::external
    end

    %% Data Flow
    User -->|invokes Pipeline/Module| API
    API -->|"construct Pipeline"| PipelineMgr
    PipelineMgr -->|"uses primitives"| Primitives
    PipelineMgr -->|"orchestrates modules"| Modules
    PipelineMgr -->|"returns results"| User

    Modules -->|"call adapters"| Adapters
    Adapters -->|"use types"| Types
    Modules -->|"invoke clients"| Clients
    Modules -->|"invoke retrieval"| Retrieval
    PipelineMgr -->|"use optimizers"| Teleprompt
    Teleprompt -->|"inference calls"| Clients

    Retrieval -->|"vector-store calls"| VectorDB
    Clients -->|"inference requests"| LMAPI

    Evaluation -->|"plug-in metrics"| PipelineMgr
    Evaluation -->|"plug-in metrics"| Modules

    Utilities -->|"support functions"| Adapters
    Utilities -->|"support functions"| Clients
    Utilities -->|"support functions"| Retrieval

    %% Click Events
    click API "https://github.com/stanfordnlp/dspy/blob/main/dspy/__init__.py"
    click Primitives "https://github.com/stanfordnlp/dspy/tree/main/dspy/primitives/"
    click Modules "https://github.com/stanfordnlp/dspy/tree/main/dspy/predict/"
    click PipelineMgr "https://github.com/stanfordnlp/dspy/blob/main/dspy/predict/predict.py"
    click PipelineMgr "https://github.com/stanfordnlp/dspy/blob/main/dspy/predict/retry.py"
    click Adapters "https://github.com/stanfordnlp/dspy/tree/main/dspy/adapters/"
    click Types "https://github.com/stanfordnlp/dspy/tree/main/dspy/adapters/types/"
    click Clients "https://github.com/stanfordnlp/dspy/tree/main/dspy/clients/"
    click Retrieval "https://github.com/stanfordnlp/dspy/tree/main/dspy/retrieve/"
    click Teleprompt "https://github.com/stanfordnlp/dspy/tree/main/dspy/teleprompt/"
    click Evaluation "https://github.com/stanfordnlp/dspy/tree/main/dspy/evaluate/"
    click Experimental "https://github.com/stanfordnlp/dspy/tree/main/dspy/experimental/"
    click Utilities "https://github.com/stanfordnlp/dspy/tree/main/dspy/utils/"

    %% Styles
    classDef core fill:#D0E6FF,stroke:#034EA2,color:#034EA2
    classDef subsystem fill:#DFF5D0,stroke:#2F8F00,color:#2F8F00
    classDef utilOrange fill:#FFE8CC,stroke:#D97706,color:#D97706
    classDef external fill:#E0E0E0,stroke:#6B6B6B,color:#6B6B6B
