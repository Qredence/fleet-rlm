---
name: delegation
description: "Delegate recursive work to child RLM sandboxes with budget management. Use when decomposing tasks into sub-queries, fanning out batched work, managing LLM call budgets, or building parent-child RLM hierarchies."
---

# Delegation — Child RLM Sandboxes and Budget Management

Delegate recursive sub-tasks to child sandboxes from a parent RLM session.
Covers single-child execution, concurrent fan-out, in-sandbox recursion,
local fast-path solvers, and budget leasing.

There are **no slash commands** — all interactions use the Python API.

---

## Host-Side Delegation

### Single Child — `delegate_to_rlm`

```python
from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

result = delegate_to_rlm(
    query="Summarize the error patterns in this log file",
    context="Service: auth-gateway, Window: last 2h",
    document_url="https://storage.example.com/logs/auth-gw-2024-03.txt",
    interpreter=parent_interpreter,
)
# Returns:
# {"status": "ok"|"error"|"needs_human_review", "answer": str, ...}
```

- **Pre-check**: Rejects immediately if parent budget is exhausted
  (`_remaining_llm_budget() == 0`).
- **Result truncation**: Output truncated to `delegate_result_truncation_chars`
  (default: 8000 chars) to prevent context blowup in parent.
- **Metadata**: Result includes `child_duration_ms`, `child_sandbox_id`,
  `llm_budget_lease`.

### Batched Fan-Out — `delegate_to_rlm_batched`

```python
from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm_batched

result = delegate_to_rlm_batched(
    queries=[
        "Extract error counts from chunk 1",
        "Extract error counts from chunk 2",
        "Extract error counts from chunk 3",
    ],
    context="Service: payments",
    interpreter=parent_interpreter,
)
# Returns:
# {
#     "status": "ok",
#     "results": [...],   # successful answers
#     "errors": [...],    # failed children
#     "reviews": [...],   # needs_human_review children
# }
```

- Uses `ThreadPoolExecutor` with **max 4 workers** by default.
- Each child receives a budget lease (portion of parent's remaining budget).
- All results individually truncated to `delegate_result_truncation_chars`.

---

## In-Sandbox Delegation

Available inside Daytona sandbox code (not host Python). These let a running
session spawn recursive sub-queries without the host needing to orchestrate.

### `sub_rlm` / `rlm_query` — Single Recursive Call

```python
# Inside Daytona sandbox code:
answer = sub_rlm("What are the top 3 failure modes in this dataset?")
# or equivalently:
answer = rlm_query("What are the top 3 failure modes in this dataset?")
SUBMIT(analysis=answer)
```

### `sub_rlm_batched` / `rlm_query_batched` — Concurrent Recursive Calls

```python
# Inside Daytona sandbox code:
results = sub_rlm_batched(
    [
        "Summarize section A",
        "Summarize section B",
        "Summarize section C",
    ],
    concurrency=3,
)
# or equivalently:
results = rlm_query_batched(
    ["Summarize section A", "Summarize section B", "Summarize section C"],
    concurrency=3,
)
SUBMIT(summaries=results)
```

- `batch_concurrency` on the websocket request controls allowed parallelism.
- `sub_rlm` and `rlm_query` are identical (alternative names, same behavior).
- `sub_rlm_batched` and `rlm_query_batched` are identical.
- Do **NOT** use `parallel_semantic_map` from the Daytona path.

---

## Fast-Path Local Solvers

These bypass child sandbox creation entirely for deterministic tasks that do
not require LLM reasoning. The runtime auto-detects eligible queries and
resolves them in-process.

| Solver | Technique | Example Query |
| --- | --- | --- |
| Sentiment classification | Regex-based word matching | "Is this review positive or negative?" |
| Log extraction | Pattern matching for level+service | "Find ERROR lines from auth-service" |
| Category counting | JSON field filtering | "How many items have status=failed?" |

Fast-path solvers:
- Consume zero LLM budget.
- Return instantly (no sandbox creation overhead).
- Are selected before delegation is attempted.
- Fall through to full child delegation if confidence is below threshold.

---

## Pattern Decision — When to Use Which

| Pattern | When to Use | Workers | Volume Sharing |
| --- | --- | --- | --- |
| Single child (`delegate_to_rlm`) | Specific sub-task needing isolated execution | 1 | No (clean sandbox) |
| Batched fan-out (`delegate_to_rlm_batched`) | Multiple independent sub-tasks | max 4 | No (each gets own sandbox) |
| In-sandbox recursive (`sub_rlm` / `rlm_query`) | Sub-tasks within running session | 1-3 | Yes (shares parent volume) |
| Sequential reuse | Small workloads, reuse single interpreter | 1 | Yes (same interpreter) |

Decision flow:
1. Can a fast-path local solver handle it? Use that (no child needed).
2. Is the task within a running sandbox? Use `sub_rlm` / `sub_rlm_batched`.
3. Is there one isolated sub-task from the host? Use `delegate_to_rlm`.
4. Are there multiple independent sub-tasks from the host? Use `delegate_to_rlm_batched`.

---

## Concurrency Limits

- **Daytona workspace limits**: Start with 2-3 workers for concurrent
  delegation. Daytona has per-account workspace caps; exceeding them causes
  sandbox creation failures.
- **Budget leases**: Parent budget is subdivided among children via
  `_delegate_budget_leases()`. Each child gets a proportional share.
- **Timeout**: 180s per broker call by default. Configurable via
  `delegate_timeout_s` on the interpreter or `FLEET_RLM_DELEGATE_TIMEOUT`
  environment variable.
- **Recursion depth**: `max_recursion_depth` (default: 2) prevents unbounded
  nesting. A child at depth 2 cannot spawn further children.

---

## See Also

- **sandbox-execution** — Interpreter lifecycle, `DaytonaInterpreter` start/shutdown
- **dspy-programs** — Child signature selection, module wiring for delegated tasks
