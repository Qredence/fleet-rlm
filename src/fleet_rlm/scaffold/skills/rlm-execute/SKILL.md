---
name: rlm-execute
description: Execute Python code in Daytona sandboxes with durable volume persistence. Use when running code in a Daytona sandbox, processing data with stateful execution, or persisting results across sessions.
---

# RLM Execute

Execute Python code in Daytona sandboxes with volume persistence.

## Basic Execution

```python
from fleet_rlm.runtime.config import configure_planner_from_env
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

configure_planner_from_env()

interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    timeout=600,
)
interp.start()
try:
    result = interp.execute('''
import math
SUBMIT(answer=math.factorial(15))
''')
    print(result.answer)  # 1307674368000
finally:
    interp.shutdown()
```

## With Durable Volume Persistence

Data written to `/home/daytona/memory/` persists across sessions on the
mounted Daytona volume.

```python
interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    volume_name="my-data",
    timeout=600,
)
interp.start()
try:
    # Write data
    interp.execute('''
import json, os
os.makedirs('/home/daytona/memory/artifacts', exist_ok=True)
data = {"processed": True, "count": 42}
with open('/home/daytona/memory/artifacts/results.json', 'w') as f:
    json.dump(data, f)
SUBMIT(status="saved")
''')

    # Read it back in the same session (or a later one)
    result = interp.execute('''
import json
with open('/home/daytona/memory/artifacts/results.json') as f:
    data = json.load(f)
SUBMIT(data=data)
''')
    print(result.data)  # {"processed": True, "count": 42}
finally:
    interp.shutdown()
```

## Execute a Local File

```python
# Read local file, execute in sandbox
code = open("scripts/analysis.py").read()
interp.start()
try:
    result = interp.execute(code)
finally:
    interp.shutdown()
```

## Execution Patterns

### Data Processing Pipeline

```python
interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    volume_name="pipeline",
    timeout=600,
)
interp.start()
try:
    # Step 1: Generate data
    interp.execute('''
import json, os
os.makedirs('/home/daytona/memory/buffers', exist_ok=True)
data = [{"id": i, "value": i**2} for i in range(100)]
with open('/home/daytona/memory/buffers/raw.json', 'w') as f:
    json.dump(data, f)
SUBMIT(count=len(data))
''')

    # Step 2: Process (same sandbox)
    result = interp.execute('''
import json
with open('/home/daytona/memory/buffers/raw.json') as f:
    data = json.load(f)
filtered = [d for d in data if d["value"] > 50]
SUBMIT(filtered_count=len(filtered))
''')
    print(result.filtered_count)
finally:
    interp.shutdown()
```

### Multi-Step with Buffers

`add_buffer(name, value)` / `get_buffer(name)` are injected by the
sandbox driver (`runtime/execution/sandbox_assets.py`) and persist
named lists across `execute()` calls in the same sandbox session:

```python
interp.start()
try:
    # Buffers persist across execute() calls within same sandbox
    interp.execute('add_buffer("findings", "Step 1: setup complete")')
    interp.execute('add_buffer("findings", "Step 2: data loaded")')
    result = interp.execute('''
items = get_buffer("findings")
SUBMIT(log=items)
''')
    print(result.log)  # ["Step 1: setup complete", "Step 2: data loaded"]
finally:
    interp.shutdown()
```

## Key Points

- Access results via `result.field_name` (dot notation), not `result["field"]`
- Data in `/home/daytona/memory/` persists across sessions when using `volume_name`
- Buffers (`add_buffer`/`get_buffer`) persist within a single sandbox session only
- Always call `interp.shutdown()` in a `finally` block
- Set `timeout` appropriately for long-running tasks; default is 900s

## Troubleshooting

See `rlm-debug` for runtime failures and `daytona-runtime` for Daytona-specific execution rules.
