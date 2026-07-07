---
name: sandbox-execution
description: "Execute Python in Daytona sandboxes with durable volume persistence. Use when running code, persisting results, inspecting volumes, managing workspace lifecycle, or validating Daytona connectivity."
---

## DaytonaInterpreter

| Parameter | Default | Description |
|-----------|---------|-------------|
| `timeout` | 900 | Sandbox lifetime seconds |
| `execute_timeout` | None | Per-execute timeout, defaults to timeout |
| `volume_name` | None | Durable volume name for cross-session persistence |
| `repo_url` | None | Repo to stage into sandbox |
| `repo_ref` | None | Branch/commit for staging |
| `context_paths` | None | Specific paths to stage |
| `max_llm_calls` | 50 | Semantic sub-LM call-count budget |
| `llm_call_timeout` | 60 | Per LLM call timeout |
| `async_execute` | True | Use async execution path |
| `max_recursion_depth` | 2 | Delegation depth limit |
| `delegate_max_iterations` | 8 | Per-child RLM iterations |

## SUBMIT Protocol

- Code must call `SUBMIT(field=value, ...)` to return results.
- Access results via dot notation: `result.field_name` (NOT `result["field"]`).
- All output fields must be serializable (str, int, float, list, dict).

## Volume Root

- Durable volume at `/home/daytona/memory/`.
- Reference: see `references/volume-layout.md` for full directory table.
- Live workspace is transient; only volume persists across sessions.

## Buffer Pattern

- `add_buffer(name, value)` / `get_buffer(name)` — injected by sandbox driver.
- Persist named lists across `execute()` calls within same session.
- Buffers are session-scoped (not durable across sessions).

## Smoke Test

```bash
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

Run before depending on live Daytona behavior.

## Quick Patterns

### Basic execution

```python
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

interpreter = DaytonaInterpreter(timeout=300)
try:
    await interpreter.start()
    result = await interpreter.execute("""
import math
SUBMIT(answer=math.factorial(10), status="ok")
""")
    print(result.answer)  # 3628800
finally:
    await interpreter.shutdown()
```

### Volume persistence

```python
interpreter = DaytonaInterpreter(timeout=300, volume_name="my-project")
try:
    await interpreter.start()
    # Write to durable volume
    await interpreter.execute("""
import json, pathlib
out = pathlib.Path("/home/daytona/memory/knowledge/report.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"key": "value"}))
SUBMIT(saved=str(out))
""")
    # Read back in a later call (or even a later session with same volume_name)
    result = await interpreter.execute("""
import json, pathlib
data = json.loads(pathlib.Path("/home/daytona/memory/knowledge/report.json").read_text())
SUBMIT(data=data)
""")
    print(result.data)  # {"key": "value"}
finally:
    await interpreter.shutdown()
```

### Multi-step with buffers

```python
interpreter = DaytonaInterpreter(timeout=600)
try:
    await interpreter.start()
    # Step 1: collect items
    await interpreter.execute("""
add_buffer("findings", "First observation")
add_buffer("findings", "Second observation")
SUBMIT(step="collected")
""")
    # Step 2: aggregate
    result = await interpreter.execute("""
items = get_buffer("findings")
SUBMIT(count=len(items), items=items)
""")
    print(result.items)  # ["First observation", "Second observation"]
finally:
    await interpreter.shutdown()
```

## See Also

- **delegation** — when sandbox code needs child RLMs
- **diagnostics** — when execution fails
