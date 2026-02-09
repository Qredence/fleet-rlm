---
name: rlm-execute
description: Execute Python code in Modal sandboxes with automatic volume persistence. Use when running code in a cloud sandbox, processing data with stateful execution, or persisting results across sessions.
---

# RLM Execute

Execute Python code in Modal sandboxes with volume persistence.

## Basic Execution

```python
from fleet_rlm import ModalInterpreter

with ModalInterpreter(timeout=600) as interp:
    result = interp.execute('''
import math
SUBMIT(answer=math.factorial(15))
''')
    print(result.answer)  # 1307674368000
```

## With Volume Persistence

```python
with ModalInterpreter(timeout=600, volume_name="my-data") as interp:
    # Write data
    interp.execute('''
import json
data = {"processed": True, "count": 42}
with open('/data/results.json', 'w') as f:
    json.dump(data, f)
SUBMIT(status="saved")
''')

# Later session - data persists
with ModalInterpreter(timeout=600, volume_name="my-data") as interp:
    result = interp.execute('''
import json
with open('/data/results.json') as f:
    data = json.load(f)
SUBMIT(data=data)
''')
    print(result.data)  # {"processed": True, "count": 42}
```

## Execute a Local File

```python
# Read local file, execute in sandbox
code = open("scripts/analysis.py").read()
with ModalInterpreter(timeout=600) as interp:
    result = interp.execute(code)
```

## CLI Execution

```bash
# Basic task
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"

# With volume
uv run fleet-rlm run-basic \
    --question "Calculate factorial of 20" \
    --volume-name my-data
```

## Execution Patterns

### Data Processing Pipeline

```python
with ModalInterpreter(timeout=600, volume_name="pipeline") as interp:
    # Step 1: Generate data
    interp.execute('''
import json
data = [{"id": i, "value": i**2} for i in range(100)]
with open('/data/raw.json', 'w') as f:
    json.dump(data, f)
SUBMIT(count=len(data))
''')

    # Step 2: Process (same sandbox, same volume)
    result = interp.execute('''
import json
with open('/data/raw.json') as f:
    data = json.load(f)
filtered = [d for d in data if d["value"] > 50]
SUBMIT(filtered_count=len(filtered))
''')
    print(result.filtered_count)
```

### Multi-Step with Buffers

```python
with ModalInterpreter(timeout=600) as interp:
    # Buffers persist across execute() calls within same sandbox
    interp.execute('add_buffer("findings", "Step 1: setup complete")')
    interp.execute('add_buffer("findings", "Step 2: data loaded")')
    result = interp.execute('''
items = get_buffer("findings")
SUBMIT(log=items)
''')
    print(result.log)  # ["Step 1: setup complete", "Step 2: data loaded"]
```

## Key Points

- Access results via `result.field_name` (dot notation), not `result["field"]`
- Data in `/data/` persists across sessions when using `volume_name`
- Buffers (`add_buffer`/`get_buffer`) persist within a single sandbox session
- Always use `with` statement or call `interp.shutdown()` for cleanup
- Set `timeout` appropriately for long-running tasks

## Troubleshooting

See `rlm-debug` skill for comprehensive diagnostics.
