---
name: rlm-run
description: Run fleet-rlm through its current public entrypoints. Use when you need the right command for the Web UI, API server, MCP server, terminal chat, or Daytona smoke validation from a Claude Code workflow.
---

# RLM Runner

Use this skill for current entrypoint selection, not legacy `run-*` demos.

## Public Entry Points

```bash
# from repo root
uv run fleet web
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm serve-mcp --transport stdio
uv run fleet-rlm chat
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

## How To Choose

- `fleet web` when the task is workspace-first and UI driven
- `serve-api` when the backend surface itself is what you need
- `serve-mcp` when an MCP client should talk to fleet-rlm tools
- `chat` for in-process terminal interaction
- `daytona-smoke` before any `daytona_pilot` workflow

## Programmatic Usage

```python
import dspy
from fleet_rlm import ModalInterpreter, configure_planner_from_env

configure_planner_from_env()  # Load .env and configure the planner LM

interpreter = ModalInterpreter(timeout=120, volume_name="my-project")
rlm = dspy.RLM(
    signature="question -> answer, confidence",
    interpreter=interpreter,
    max_iterations=10,
    max_llm_calls=20,
    verbose=True,  # Show trajectory
)

result = rlm(question="What are the first 10 Fibonacci numbers?")
print(result.answer)       # Access via dot notation
print(result.confidence)   # NOT result["confidence"]
```

## Configuration Options

| Parameter        | Description               | Default            |
| ---------------- | ------------------------- | ------------------ |
| `signature`      | Input/output fields       | `"task -> result"` |
| `max_iterations` | Max RLM iterations        | 10                 |
| `max_llm_calls`  | Max sub-LLM calls         | 20                 |
| `timeout`        | Sandbox timeout (seconds) | 120                |
| `verbose`        | Show full trajectory      | False              |
| `volume_name`    | Volume for persistence    | None               |

## Runtime Reminder

- `modal_chat` is the default runtime path
- `daytona_pilot` is the Daytona-backed variant of the same shared runtime
- If the task is Daytona-specific, also load `daytona-runtime`

## Execution Patterns

### Simple Task

```python
rlm = dspy.RLM(
    signature="question -> answer",
    interpreter=ModalInterpreter(timeout=60),
    max_iterations=5,
)
result = rlm(question="What is 15 factorial?")
print(result.answer)
```

### Document Summarization

```python
from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument

doc = open("large_document.txt").read()
rlm = dspy.RLM(
    signature=SummarizeLongDocument,
    interpreter=ModalInterpreter(timeout=300, volume_name="analysis"),
    max_iterations=20,
    verbose=True,
)
result = rlm(document=doc, focus="Find key design decisions")
print(result.key_points)
print(result.answer)
```

### Trajectory Inspection

```python
result = rlm(question="Complex task")
trajectory = getattr(result, "trajectory", [])
for i, step in enumerate(trajectory):
    print(f"Step {i+1}: {step}")
```

## Troubleshooting

See `rlm-debug` for runtime failures and `daytona-runtime` for Daytona-specific execution rules.
