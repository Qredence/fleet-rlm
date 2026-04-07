---
name: rlm-run
description: Run fleet-rlm through its current public entrypoints. Use when you need the right command for the Web UI, API server, MCP server, terminal chat, or Daytona smoke validation from a Claude Code workflow.
---

# RLM Runner

Use this skill for current entrypoint selection.

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
from fleet_rlm.runtime.config import configure_planner_from_env
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

configure_planner_from_env()  # Load .env and configure the planner LM

interpreter = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    volume_name="my-project",
    timeout=120,
)
rlm = dspy.RLM(
    signature="question -> answer, confidence",
    interpreter=interpreter,
    max_iterations=10,
    max_llm_calls=20,
    verbose=True,  # Show trajectory
)

try:
    result = rlm(question="What are the first 10 Fibonacci numbers?")
    print(result.answer)       # Access via dot notation
    print(result.confidence)   # NOT result["confidence"]
finally:
    interpreter.shutdown()
```

## Configuration Options

| Parameter        | Description                    | Default            |
| ---------------- | ------------------------------ | ------------------ |
| `signature`      | Input/output fields            | `"task -> result"` |
| `max_iterations` | Max RLM iterations             | 10                 |
| `max_llm_calls`  | Max sub-LLM calls              | 20                 |
| `timeout`        | Sandbox timeout (seconds)      | 900                |
| `verbose`        | Show full trajectory           | False              |
| `volume_name`    | Daytona persistent volume name | None               |
| `repo_url`       | Repo to stage into sandbox     | None               |
| `repo_ref`       | Branch/commit for repo staging | None               |
| `context_paths`  | Paths to stage from repo       | None               |

## Runtime

- `daytona_pilot` is the primary runtime path
- Daytona is the interpreter/sandbox backend on the shared ReAct + `dspy.RLM` backbone
- Load `daytona-runtime` for Daytona-specific volume, session, and smoke-test guidance

## Execution Patterns

### Simple Task

```python
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

interp = DaytonaInterpreter(timeout=60)
interp.start()
try:
    rlm = dspy.RLM(
        signature="question -> answer",
        interpreter=interp,
        max_iterations=5,
    )
    result = rlm(question="What is 15 factorial?")
    print(result.answer)
finally:
    interp.shutdown()
```

### Document Summarization

```python
from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

doc = open("large_document.txt").read()
interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    volume_name="analysis",
    timeout=300,
)
interp.start()
try:
    rlm = dspy.RLM(
        signature=SummarizeLongDocument,
        interpreter=interp,
        max_iterations=20,
        verbose=True,
    )
    result = rlm(document=doc, focus="Find key design decisions")
    print(result.key_points)
    print(result.summary)
finally:
    interp.shutdown()
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
