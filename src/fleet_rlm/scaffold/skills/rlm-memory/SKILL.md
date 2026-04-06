---
name: rlm-memory
description: Long-term memory persistence for RLM using Daytona durable volume storage. Use when storing, recalling, listing, or searching data that persists across sandbox sessions in the daytona_pilot runtime.
---

# RLM Memory — Persistent Storage

Persist data across sandbox sessions using the Daytona mounted durable volume.

There are **no slash commands** — all interactions use the Python API.

---

## Daytona Durable Volume

The Daytona mounted volume is rooted at `/home/daytona/memory/` inside the sandbox.

### Canonical Directories

| Directory                         | Purpose                                    |
| --------------------------------- | ------------------------------------------ |
| `/home/daytona/memory/memory/`    | Key-value and named memory items           |
| `/home/daytona/memory/artifacts/` | Produced outputs and saved results         |
| `/home/daytona/memory/buffers/`   | Named buffer lists (session logs, staging) |
| `/home/daytona/memory/meta/`      | Session manifests and workspace metadata   |

### Session Manifest Path

```
/home/daytona/memory/meta/workspaces/<workspace_id>/users/<user_id>/react-session-<session_id>.json
```

---

## Store and Recall

```python
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    timeout=120,
    volume_name="rlm-volume-dspy",
)
interp.start()
try:
    interp.execute('''
import json, os
os.makedirs('/home/daytona/memory/artifacts', exist_ok=True)
data = {'result': 'my-finding', 'created': '2026-04-03'}
with open('/home/daytona/memory/artifacts/analysis.json', 'w') as f:
    json.dump(data, f)
''')

    result = interp.execute('''
import json
with open('/home/daytona/memory/artifacts/analysis.json') as f:
    data = json.load(f)
SUBMIT(result=data['result'])
''')
    print(result.result)
finally:
    interp.shutdown()
```

---

## Buffer Pattern

`add_buffer(name, value)` / `get_buffer(name)` are injected by the sandbox
driver (`runtime/execution/sandbox_assets.py`) and persist named lists across
`execute()` calls within the same sandbox session:

```python
interp.start()
try:
    interp.execute('add_buffer("log", "Step 1: setup")')
    interp.execute('add_buffer("log", "Step 2: done")')
    result = interp.execute('SUBMIT(log=get_buffer("log"))')
    print(result.log)  # ["Step 1: setup", "Step 2: done"]
finally:
    interp.shutdown()
```

---

## Volume-Aware Document Tools (Inside Sandbox)

Available inside sandbox code on the Daytona path:

- `load_text_from_volume(path)` — loads durable mounted-volume content
- `process_document(...)` — targeted at mounted-volume content
- `workspace_read(path)` — low-level transient workspace helper (not durable)

---

## Checkpoint Pattern

```python
interp.start()
try:
    # Save intermediate results during long workflows
    interp.execute('''
import json, os
root = '/home/daytona/memory/buffers/checkpoints'
os.makedirs(root, exist_ok=True)
with open(f'{root}/batch_1.json', 'w') as f:
    json.dump([{'i': i} for i in range(100)], f)
''')

    # Resume in next execute() call or next session
    result = interp.execute('''
import json
with open('/home/daytona/memory/buffers/checkpoints/batch_1.json') as f:
    previous = json.load(f)
SUBMIT(count=len(previous))
''')
    print(result.count)  # 100
finally:
    interp.shutdown()
```

---

## Troubleshooting

See `rlm-debug` for comprehensive diagnostics and `daytona-runtime` for Daytona volume specifics.
