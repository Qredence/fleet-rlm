# Budget Model — LLM Call Budget and Lease Management

## Budget Ownership

`max_llm_calls` is the semantic budget owned by the parent interpreter. It
represents the total number of LLM calls the interpreter is allowed to make
during its lifetime (including calls made by delegated children).

```python
# Set at interpreter construction:
interp = DaytonaInterpreter(
    repo_url="...",
    max_llm_calls=30,  # Parent owns 30 calls total
)
```

## Querying Remaining Budget

```python
remaining = interpreter._remaining_llm_budget()
# Returns: max_llm_calls - current_count
```

This is a live value — it decreases as the interpreter (or its children)
consume calls.

## Tracking and Enforcement

```python
interpreter._check_and_increment_llm_calls()
```

- Increments the internal counter by 1.
- Raises `BudgetExhaustedError` if `current_count >= max_llm_calls`.
- Called automatically on each LLM invocation within the interpreter.

## Budget Lease Algorithm

When delegating to children, the parent subdivides its remaining budget:

```python
leases = interpreter._delegate_budget_leases(num_children=3)
# Example: if remaining=18 and num_children=3
# Returns: [6, 6, 6]
```

Algorithm:
1. Compute `remaining = _remaining_llm_budget()`.
2. Divide evenly: `per_child = remaining // num_children`.
3. Remainder distributed to first children (round-robin).
4. Each child interpreter is constructed with `max_llm_calls=per_child`.

## Thread Safety

- Each child interpreter is an independent instance with its own counter.
- No shared state across children — counters are per-instance.
- Parent's counter is only decremented by the parent's own calls, not by
  children's consumption (children have their own budgets from the lease).

## Rejection Policy

When budget is exhausted (`_remaining_llm_budget() == 0`):
- Delegation is **rejected upfront** — `delegate_to_rlm` raises before
  creating any child sandbox.
- No partial execution occurs.
- The caller receives a `BudgetExhaustedError` with the current budget state.

This prevents wasted sandbox creation and ensures predictable cost boundaries.

## Metadata Attached to Results

Every delegation result includes budget metadata:

```python
{
    "status": "ok",
    "answer": "...",
    "llm_budget_lease": 6,         # How many calls the child was allowed
    "child_duration_ms": 4200,     # Wall-clock time for child execution
    "child_sandbox_id": "ws-abc",  # Daytona workspace ID used
}
```

This enables:
- Post-hoc analysis of budget utilization per child.
- Identifying children that consumed their full lease (potential underfunding).
- Correlating execution time with budget size for tuning.
