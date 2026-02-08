---
name: rlm-run
description: Run RLM tasks with proper configuration. Use when executing tasks with dspy.RLM, configuring ModalInterpreter options, managing timeouts and iterations, or running predefined fleet-rlm CLI commands.
---

# RLM Runner

Execute dspy.RLM tasks with ModalInterpreter.

## CLI Commands

All commands use `uv run fleet-rlm`:

```bash
# Basic task
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"

# With volume persistence
uv run fleet-rlm run-basic \
    --question "Calculate factorial of 20" \
    --volume-name rlm-volume-dspy

# Architecture extraction from docs
uv run fleet-rlm run-architecture \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "Extract all modules and optimizers"

# API endpoint extraction
uv run fleet-rlm run-api-endpoints --docs-path rlm_content/dspy-knowledge/dspy-doc.txt

# Error pattern analysis
uv run fleet-rlm run-error-patterns --docs-path rlm_content/dspy-knowledge/dspy-doc.txt

# Long-context analysis
uv run fleet-rlm run-long-context \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "What are the main design decisions?" \
    --mode analyze

# Long-context summarization
uv run fleet-rlm run-long-context \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "DSPy optimizers" \
    --mode summarize

# Trajectory inspection
uv run fleet-rlm run-trajectory \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --chars 5000

# Custom tool demo
uv run fleet-rlm run-custom-tool \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --chars 5000
```

## Programmatic Usage

```python
import dspy
from fleet_rlm import ModalInterpreter
from fleet_rlm.config import configure

configure()  # Load .env

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

## Built-in Signatures

See `dspy-signature` skill and `src/fleet_rlm/signatures.py` for full details.

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

### Document Analysis

```python
from fleet_rlm.signatures import AnalyzeLongDocument

doc = open("large_document.txt").read()
rlm = dspy.RLM(
    signature=AnalyzeLongDocument,
    interpreter=ModalInterpreter(timeout=300, volume_name="analysis"),
    max_iterations=20,
    verbose=True,
)
result = rlm(document=doc, query="Find key design decisions")
print(result.findings)
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

See `rlm-debug` skill for comprehensive diagnostics.
