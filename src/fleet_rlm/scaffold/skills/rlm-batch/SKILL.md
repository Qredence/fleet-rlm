---
name: rlm-batch
description: Execute multiple Python tasks in parallel using Modal sandboxes. Use when batch processing data pipelines, running parameter sweeps, or distributing computation across sandboxes.
---

# RLM Batch — Parallel Task Execution

Run multiple independent tasks in parallel, each in its own Modal sandbox.

There are **no slash commands** — all interactions use `ModalInterpreter` Python API.

---

## Concept

Each sandbox is independent. To run N tasks in parallel:

1. Create N `ModalInterpreter` instances (or reuse one sequentially).
2. Execute code in each.
3. Collect results.

For true parallelism, use Python `concurrent.futures`:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from fleet_rlm import ModalInterpreter

tasks = [
    "result = sum(range(1000)); SUBMIT(total=result)",
    "import math; SUBMIT(pi_approx=math.pi)",
    "SUBMIT(greeting='Hello from sandbox')",
]

def run_task(code):
    with ModalInterpreter(timeout=120) as interp:
        return interp.execute(code)

with ThreadPoolExecutor(max_workers=4) as pool:
    futures = {pool.submit(run_task, t): i for i, t in enumerate(tasks)}
    for future in as_completed(futures):
        idx = futures[future]
        result = future.result()
        print(f"Task {idx}: {result}")
```

---

## Batch with Shared Volume

All tasks read/write to the same persistent volume:

```python
from concurrent.futures import ThreadPoolExecutor
from fleet_rlm import ModalInterpreter

VOLUME = 'rlm-volume-dspy'

def process_chunk(chunk_id, data):
    code = (
        f"import json\n"
        f"data = {repr(data)}\n"
        f"processed = [x * 2 for x in data]\n"
        f"save_to_volume(f'results/chunk_{chunk_id}.json', json.dumps(processed))\n"
        f"SUBMIT(chunk_id={chunk_id}, count=len(processed))"
    )
    with ModalInterpreter(timeout=120, volume_name=VOLUME) as interp:
        return interp.execute(code)

chunks = {0: [1,2,3], 1: [4,5,6], 2: [7,8,9]}

with ThreadPoolExecutor(max_workers=3) as pool:
    futures = [pool.submit(process_chunk, cid, data) for cid, data in chunks.items()]
    for f in futures:
        r = f.result()
        print(f"Chunk {r.chunk_id}: {r.count} items")
```

---

## Parameter Sweep

```python
from concurrent.futures import ThreadPoolExecutor
from fleet_rlm import ModalInterpreter

def sweep(lr, batch_size):
    code = (
        "import random\n"
        "random.seed(42)\n"
        f"loss = 1.0 / ({lr} * {batch_size} + 0.01) + random.random() * 0.1\n"
        f"SUBMIT(lr={lr}, batch_size={batch_size}, loss=loss)"
    )
    with ModalInterpreter(timeout=300) as interp:
        return interp.execute(code)

params = [(0.001, 32), (0.01, 32), (0.001, 64), (0.01, 64)]

with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(lambda p: sweep(*p), params))
    best = min(results, key=lambda r: r.loss)
    print(f"Best: lr={best.lr}, batch={best.batch_size}, loss={best.loss:.4f}")
```

---

## Sequential Batch (Simple)

For smaller workloads, reuse a single interpreter:

```python
from fleet_rlm import ModalInterpreter

with ModalInterpreter(timeout=300) as interp:
    for i in range(5):
        result = interp.execute(
            f"import math; SUBMIT(task={i}, value=math.factorial({i + 10}))"
        )
        print(f"Task {result.task}: {result.value}")
```

---

## Tips

1. **Limit max_workers**: Modal has concurrency limits; start with 4-8
2. **Use volumes for large outputs**: Don't pass huge data through SUBMIT
3. **Handle failures**: Wrap in try/except for individual task retries
