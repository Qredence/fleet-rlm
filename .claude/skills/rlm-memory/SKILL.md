---
name: rlm-memory
description: Long-term memory persistence for RLM using Modal Volume-backed storage. Use when storing, recalling, listing, or searching data that persists across sandbox sessions.
---

# RLM Memory — Volume-Backed Persistent Storage

Persist data across Modal sandbox sessions using Volumes V2. All operations
run inside the sandbox via `ModalInterpreter.execute()` or use the injected
`save_to_volume` / `load_from_volume` helpers.

There are **no slash commands** — all interactions use the Python API or CLI.

---

## Prerequisites

1. **Modal Volume created** (one-time):
   ```bash
   uv run modal volume create rlm-volume-dspy
   ```
2. **Dependencies installed**: `uv sync`

---

## Core Concepts

- Volumes are mounted at `/data/` inside the sandbox.
- The `/data/memory/` directory is the convention for key-value memory storage.
- `save_to_volume(path, content)` and `load_from_volume(path)` are sandbox-side
  helpers injected by the driver — they work with paths relative to `/data/`.
- Data persists automatically when the sandbox shuts down (Volumes V2).

---

## Usage Patterns

### Pattern 1: Store and Recall via Sandbox Helpers

```python
from fleet_rlm import ModalInterpreter

with ModalInterpreter(timeout=120, volume_name='rlm-volume-dspy') as interp:
    # Store a value
    interp.execute('''
import json, os
os.makedirs('/data/memory', exist_ok=True)
data = {'value': 'my-important-result', 'created': '2024-01-15'}
save_to_volume('memory/analysis.json', json.dumps(data))
print('Stored successfully')
''')

    # Recall the value
    result = interp.execute('''
import json
raw = load_from_volume('memory/analysis.json')
data = json.loads(raw)
print(f"Value: {data['value']}")
''')
    print(result)
```

### Pattern 2: Direct File Operations

```python
with ModalInterpreter(timeout=120, volume_name='rlm-volume-dspy') as interp:
    # Write directly to /data/
    interp.execute('''
import json, os
os.makedirs('/data/memory', exist_ok=True)
with open('/data/memory/config.json', 'w') as f:
    json.dump({'timeout': 300, 'retries': 3}, f)
print('Config saved')
''')

    # Read back
    result = interp.execute('''
import json
with open('/data/memory/config.json') as f:
    config = json.load(f)
SUBMIT(config=config)
''')
    print(result.config)  # {'timeout': 300, 'retries': 3}
```

### Pattern 3: List All Memories

```python
with ModalInterpreter(timeout=120, volume_name='rlm-volume-dspy') as interp:
    result = interp.execute('''
import os

memory_dir = '/data/memory'
if not os.path.exists(memory_dir):
    SUBMIT(memories=[])
else:
    memories = []
    for fname in sorted(os.listdir(memory_dir)):
        fpath = os.path.join(memory_dir, fname)
        stat = os.stat(fpath)
        memories.append({'key': fname.replace('.json', ''), 'size_bytes': stat.st_size})
    SUBMIT(memories=memories)
''')
    for m in result.memories:
        print(f"  {m['key']}: {m['size_bytes']} bytes")
```

### Pattern 4: Search Memories by Content

```python
with ModalInterpreter(timeout=120, volume_name='rlm-volume-dspy') as interp:
    result = interp.execute('''
import os, re

query = 'error'
memory_dir = '/data/memory'
matches = []
if os.path.exists(memory_dir):
    for fname in os.listdir(memory_dir):
        with open(os.path.join(memory_dir, fname)) as f:
            if re.search(query, f.read(), re.IGNORECASE):
                matches.append(fname.replace('.json', ''))
SUBMIT(matches=matches, query=query)
''')
    print(f"Keys matching: {result.matches}")
```

### Pattern 5: Delete a Memory

```python
with ModalInterpreter(timeout=120, volume_name='rlm-volume-dspy') as interp:
    interp.execute('''
import os
path = '/data/memory/old_result.json'
if os.path.exists(path):
    os.remove(path)
    print('Deleted')
else:
    print('Not found')
''')
```

---

## Checkpoint Pattern

Save intermediate results during long RLM workflows:

```python
with ModalInterpreter(timeout=600, volume_name='rlm-volume-dspy') as interp:
    # Step 1: Process batch and checkpoint
    interp.execute('''
import json, os
os.makedirs('/data/checkpoints', exist_ok=True)
results = [{'item': i, 'processed': True} for i in range(100)]
save_to_volume('checkpoints/batch_1.json', json.dumps(results))
print(f'Checkpoint 1: {len(results)} items')
''')

    # Step 2: Resume from checkpoint
    result = interp.execute('''
import json
raw = load_from_volume('checkpoints/batch_1.json')
previous = json.loads(raw)
more = [{'item': i, 'processed': True} for i in range(100, 200)]
combined = previous + more
save_to_volume('checkpoints/batch_2.json', json.dumps(combined))
SUBMIT(total_processed=len(combined))
''')
    print(f"Total: {result.total_processed}")
```

---

## Upload Local Files to Volume

Use `upload_to_volume()` on the interpreter (host-side, before sandbox code):

```python
interp = ModalInterpreter(volume_name='rlm-volume-dspy')
interp.start()

interp.upload_to_volume(
    local_dirs={'rlm_content/dspy-knowledge': '/dspy-knowledge'},
    local_files={'config/config.yaml': '/config.yaml'},
)

result = interp.execute('''
import os
SUBMIT(files=os.listdir('/data/dspy-knowledge'))
''')
print(result.files)
interp.shutdown()
```

---

## Helper Functions (from rlm_helpers.py)

```python
from fleet_rlm.rlm_helpers import (
    get_default_volume_name,   # -> 'rlm-data-{user}-{workspace}'
    sanitize_key,              # -> filesystem-safe key string
    create_interpreter,        # -> ModalInterpreter with auto-volume
)
```

---

## Volume CLI Management

```bash
# List volumes
uv run modal volume list

# Create a volume
uv run modal volume create rlm-volume-dspy

# List files on a volume
uv run modal volume ls rlm-volume-dspy

# Delete a volume (destructive)
uv run modal volume delete rlm-volume-dspy
```

---

## Troubleshooting

See `rlm-debug` skill for comprehensive diagnostics.
