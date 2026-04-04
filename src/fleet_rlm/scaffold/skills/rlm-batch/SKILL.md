---
name: rlm-batch
description: Execute batched or parallel RLM work using Daytona sandboxes. Use when running multiple sub-tasks, parameter sweeps, or batch recursive RLM calls in the daytona_pilot runtime.
---

# RLM Batch — Parallel and Batched Execution

Run multiple independent tasks using Daytona sandboxes, or use
`rlm_query_batched` for batched recursive sub-work inside a Daytona session.

There are **no slash commands** — all interactions use the Daytona Python API.

---

## Concept

Batching has two shapes in `daytona_pilot`:

1. **`rlm_query_batched`** (inside sandbox code) — for concurrent recursive
   sub-queries from within a running Daytona session.
2. **Multiple `DaytonaInterpreter` instances** (host-side) — for independent
   parallel execution jobs on the host.

---

## Pattern 1: `rlm_query_batched` Inside Sandbox

Use from within Daytona sandbox code when the agent needs to delegate multiple
recursive sub-tasks concurrently.

```python
# Inside Daytona sandbox code (not host Python):
results = rlm_query_batched(
    [
        "summarize this chunk: ...",
        "extract errors from: ...",
        "identify patterns in: ...",
    ],
    concurrency=3,
)
SUBMIT(summaries=results)
```

`batch_concurrency` on the initial websocket request controls the allowed
parallelism. The public Daytona heavy-work surface is `rlm_query` /
`rlm_query_batched`. Do not use `parallel_semantic_map` from the Daytona path.

---

## Pattern 2: Parallel Host-Side Jobs

For independent jobs that each need their own sandbox, spawn multiple
`DaytonaInterpreter` instances from the host:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter

tasks = [
    "result = sum(range(1000)); SUBMIT(total=result)",
    "import math; SUBMIT(pi_approx=math.pi)",
    "SUBMIT(greeting='Hello from Daytona')",
]

def run_task(code):
    interp = DaytonaInterpreter(
        repo_url="https://github.com/your-org/your-repo",
        timeout=120,
    )
    interp.start()
    try:
        return interp.execute(code)
    finally:
        interp.shutdown()

with ThreadPoolExecutor(max_workers=3) as pool:
    futures = {pool.submit(run_task, t): i for i, t in enumerate(tasks)}
    for future in as_completed(futures):
        idx = futures[future]
        result = future.result()
        print(f"Task {idx}: {result}")
```

> **Tip**: Daytona has workspace concurrency limits; start with 2–3 workers.

---

## Pattern 3: Batch with Shared Durable Volume

All tasks write to the same persistent Daytona volume:

```python
from concurrent.futures import ThreadPoolExecutor
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter

VOLUME = 'rlm-volume-dspy'
REPO = 'https://github.com/your-org/your-repo'

def process_chunk(chunk_id, data):
    code = (
        f"import json, os\n"
        f"data = {repr(data)}\n"
        f"processed = [x * 2 for x in data]\n"
        f"os.makedirs('/home/daytona/memory/artifacts', exist_ok=True)\n"
        f"with open(f'/home/daytona/memory/artifacts/chunk_{chunk_id}.json', 'w') as f:"
        f"    json.dump(processed, f)\n"
        f"SUBMIT(chunk_id={chunk_id}, count=len(processed))"
    )
    interp = DaytonaInterpreter(repo_url=REPO, timeout=120, volume_name=VOLUME)
    interp.start()
    try:
        return interp.execute(code)
    finally:
        interp.shutdown()

chunks = {0: [1, 2, 3], 1: [4, 5, 6], 2: [7, 8, 9]}

with ThreadPoolExecutor(max_workers=3) as pool:
    futures = [pool.submit(process_chunk, cid, d) for cid, d in chunks.items()]
    for f in futures:
        r = f.result()
        print(f"Chunk {r.chunk_id}: {r.count} items")
```

---

## Parameter Sweep

```python
from concurrent.futures import ThreadPoolExecutor
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter

REPO = 'https://github.com/your-org/your-repo'

def sweep(lr, batch_size):
    code = (
        "import random\n"
        "random.seed(42)\n"
        f"loss = 1.0 / ({lr} * {batch_size} + 0.01) + random.random() * 0.1\n"
        f"SUBMIT(lr={lr}, batch_size={batch_size}, loss=loss)"
    )
    interp = DaytonaInterpreter(repo_url=REPO, timeout=300)
    interp.start()
    try:
        return interp.execute(code)
    finally:
        interp.shutdown()

params = [(0.001, 32), (0.01, 32), (0.001, 64), (0.01, 64)]

with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(lambda p: sweep(*p), params))
    best = min(results, key=lambda r: r.loss)
    print(f"Best: lr={best.lr}, batch={best.batch_size}, loss={best.loss:.4f}")
```

---

## Sequential Batch (Simple)

For smaller workloads, reuse a single interpreter across multiple executions:

```python
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter

interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    timeout=300,
)
interp.start()
try:
    for i in range(5):
        result = interp.execute(
            f"import math; SUBMIT(task={i}, value=math.factorial({i + 10}))"
        )
        print(f"Task {result.task}: {result.value}")
finally:
    interp.shutdown()
```

---

## Tips

1. **Limit `max_workers`**: Daytona has workspace concurrency limits; start with 2–3
2. **Use durable volume for large outputs**: Don’t pass huge data through SUBMIT
3. **Handle failures**: Wrap `interp.execute()` in try/except for per-task retries
4. **Prefer `rlm_query_batched` inside sandbox** for sub-tasks within a running session
