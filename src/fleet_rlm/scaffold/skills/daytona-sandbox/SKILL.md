---
name: daytona-sandbox
description: Manage Daytona sandboxes for RLM execution. Use when creating, inspecting, or cleaning up Daytona sandboxes and durable volumes used by fleet_rlm DaytonaInterpreter.
---

# Daytona Sandbox Manager

Manage Daytona sandboxes and durable volumes for fleet-rlm's DaytonaInterpreter.

## Sandbox Operations

### Create and Use a Sandbox

```python
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter

# Basic sandbox
interp = DaytonaInterpreter(timeout=600)
interp.start()
result = interp.execute('x = 42\nSUBMIT(answer=x)')
print(result.answer)  # 42
interp.shutdown()

# With repo context and volume persistence
interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    volume_name="my-project",
    timeout=600,
)
interp.start()
# ... use interp ...
interp.shutdown()
```

### List Active Workspaces

```bash
daytona list
```

### Validate Daytona Setup

```bash
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

### Terminate a Sandbox

```python
interp.shutdown()  # Clean termination (idempotent)
```

## Volume Operations

Daytona mounts a durable volume at `/home/daytona/memory/` inside the sandbox.

### Canonical Directories

| Directory | Purpose |
|---|---|
| `/home/daytona/memory/memory/` | Key-value and named memory items |
| `/home/daytona/memory/artifacts/` | Produced outputs and saved results |
| `/home/daytona/memory/buffers/` | Named buffer lists |
| `/home/daytona/memory/meta/` | Session manifests and workspace metadata |

### Inspect Volume Contents

```python
interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    volume_name="my-project",
    timeout=120,
)
interp.start()
try:
    result = interp.execute('''
import os
root = '/home/daytona/memory'
files = os.listdir(root) if os.path.exists(root) else []
SUBMIT(files=files, count=len(files))
''')
    print(result.files)
finally:
    interp.shutdown()
```

### Deep Volume Inspection

```python
interp.start()
try:
    result = interp.execute('''
import os
files = []
for root, dirs, filenames in os.walk('/home/daytona/memory'):
    for f in filenames:
        path = os.path.join(root, f)
        size = os.path.getsize(path)
        files.append(f"{path} ({size} bytes)")
SUBMIT(files=files, total=len(files))
''')
    print(f"Found {result.total} files:")
    for f in result.files:
        print(f"  {f}")
finally:
    interp.shutdown()
```

### Write to Durable Volume

```python
interp.start()
try:
    interp.execute('''
import json, os
os.makedirs('/home/daytona/memory/artifacts', exist_ok=True)
with open('/home/daytona/memory/artifacts/result.json', 'w') as f:
    json.dump({"status": "complete"}, f)
SUBMIT(saved=True)
''')
finally:
    interp.shutdown()
```

## DaytonaInterpreter Configuration

| Parameter | Default | Description |
|---|---|---|
| `timeout` | 900 | Sandbox lifetime (seconds) |
| `execute_timeout` | None | Per-execute() timeout (default: same as timeout) |
| `volume_name` | None | Durable volume name |
| `repo_url` | None | Repo to stage into sandbox |
| `repo_ref` | None | Branch/commit for repo staging |
| `context_paths` | None | Paths to stage from repo |
| `max_llm_calls` | 50 | Max LLM sub-calls |
| `llm_call_timeout` | 60 | Per LLM call timeout (seconds) |
| `async_execute` | True | Use async execution path |

### Example: Custom Configuration

```python
interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    repo_ref="feature-branch",
    context_paths=["src/", "tests/"],
    timeout=1800,
    volume_name="my-data",
    max_llm_calls=30,
)
```

## Quick Reference

| Task | Command |
|---|---|
| Create sandbox | `DaytonaInterpreter(timeout=600).start()` |
| Create with volume | `DaytonaInterpreter(volume_name="name").start()` |
| List workspaces | `daytona list` |
| Smoke test | `uv run fleet-rlm daytona-smoke --repo <url>` |
| Terminate sandbox | `interp.shutdown()` |
| Check env | `env | grep DAYTONA` |
| Check version | `daytona version` |

## Troubleshooting

See `rlm-debug` for comprehensive diagnostics and `daytona-runtime` for Daytona volume specifics.
