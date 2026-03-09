# ADR-002: Modal Sandbox Execution Model

## Status

Accepted

## Context

Fleet-RLM agents require the ability to execute arbitrary Python code during reasoning. This execution must:

1. Be isolated from the host system for security
2. Maintain state across multiple code executions within a session
3. Support persistent storage volumes for data preservation
4. Allow secure access to API keys and secrets
5. Scale to zero when idle to minimize costs
6. Support streaming output for real-time feedback

Options considered:
- **Local execution**: Security risk, no isolation, environment drift
- **Docker containers**: Requires host Docker daemon, operational complexity
- **AWS Lambda**: Stateless, limited execution time, no persistent volumes
- **Modal**: Purpose-built for this use case with sandbox environments

## Decision

We use **Modal Sandboxes** as the execution environment, wrapped by a custom `ModalInterpreter` class that implements DSPy's `CodeInterpreter` interface.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Host Process                              │
│  ┌─────────────────┐    ┌─────────────────────────────────────┐ │
│  │ RLMReActChatAgent│───▶│         ModalInterpreter            │ │
│  │  (dspy.Module)   │    │  - JSON protocol over stdin/stdout  │ │
│  └─────────────────┘    │  - Tool registration                │ │
│                          │  - Volume management                │ │
│                          └──────────────┬──────────────────────┘ │
└─────────────────────────────────────────┼───────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Modal Sandbox Process                        │
│  ┌─────────────────┐    ┌─────────────────────────────────────┐ │
│  │   sandbox_driver │◀───│     Python Code Execution           │ │
│  │  (driver.py)     │    │  - Stateful globals                 │ │
│  │  - Protocol loop │    │  - llm_query / llm_query_batched    │ │
│  │  - Tool dispatch │    │  - Volume file access               │ │
│  └─────────────────┘    └─────────────────────────────────────┘ │
│                          ┌─────────────────────────────────────┐ │
│                          │     Modal Volume (optional)         │ │
│                          │  /data/* persistent storage         │ │
│                          └─────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

#### 1. ModalInterpreter (`src/fleet_rlm/core/interpreter.py`)

The host-side adapter implementing DSPy's `CodeInterpreter` interface:

```python
class ModalInterpreter(LLMQueryMixin, VolumeOpsMixin):
    """DSPy CodeInterpreter implementation backed by a Modal sandbox."""

    def __init__(
        self,
        timeout: int = 600,
        secret_name: str = "LITELLM",
        volume_name: str | None = None,
        max_llm_calls: int = 50,
    ): ...

    def execute(self, code: str) -> Any: ...
    def start(self) -> None: ...
    def shutdown(self) -> None: ...
```

#### 2. Sandbox Driver (`src/fleet_rlm/core/driver.py`)

The sandbox-side driver that handles the JSON protocol:

- Receives code execution requests via stdin
- Executes code maintaining stateful globals
- Returns structured output via stdout
- Dispatches tool calls to registered handlers

#### 3. Built-in RLM Tools

The sandbox includes built-in tools for recursive LLM calls:

- `llm_query`: Single LLM call for sub-queries
- `llm_query_batched`: Batch LLM calls with aggregation

These tools respect the `max_llm_calls` budget to prevent runaway API costs.

### Communication Protocol

The interpreter communicates with the sandbox via JSON over stdin/stdout:

**Request:**

```json
{"type": "execute", "code": "result = 1 + 1"}
```

**Response:**

```json
{"type": "result", "value": 2, "stdout": "", "stderr": ""}
```

### Execution Profiles

The interpreter supports execution profiles that control tool exposure:

| Profile | Description |
|---------|-------------|
| `ROOT_INTERLOCUTOR` | Full tool access |
| `RLM_ROOT` | Standard RLM tool set |
| `RLM_DELEGATE` | Restricted set for child agents |
| `MAINTENANCE` | Maintenance operations |

### Volume Persistence

Optional Modal Volumes provide persistent storage:

```python
interpreter = ModalInterpreter(
    volume_name="my-data",
    volume_mount_path="/data"
)
```

Files written to `/data` persist across sandbox restarts.

## Consequences

### Positive

- **Security isolation**: Code executes in a sandboxed environment
- **Stateful execution**: Globals persist across `execute()` calls
- **Zero scaling**: Modal scales to zero when idle
- **Volume persistence**: Optional persistent storage for data
- **Secret management**: Secure access to API keys via Modal Secrets
- **Budget control**: `max_llm_calls` prevents runaway API costs

### Negative

- **Cold start latency**: Initial sandbox creation takes seconds
- **Modal dependency**: Tied to Modal platform and pricing
- **Network overhead**: JSON protocol adds serialization overhead

### Neutral

- Requires Modal credentials setup (`modal setup`)
- Default timeout is 600 seconds (configurable)
- stdout summarization is enabled by default to prevent context pollution (per RLM paper Section 2)

## References

- `src/fleet_rlm/core/interpreter.py` — ModalInterpreter implementation
- `src/fleet_rlm/core/driver.py` — Sandbox driver
- `src/fleet_rlm/core/volume_ops.py` — Volume operations
- `src/fleet_rlm/core/llm_tools.py` — Built-in LLM tools
- `src/fleet_rlm/core/sandbox_tools.py` — Sandbox tool utilities
- Modal documentation: https://modal.com/docs
