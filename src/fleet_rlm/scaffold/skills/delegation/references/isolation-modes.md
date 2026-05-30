# Isolation Modes — Child Sandbox Configuration

## ChildIsolationMode Enum

Controls how a delegated child sandbox is created relative to the parent.

| Mode | Behavior |
| --- | --- |
| `"auto"` (default) | Choose `context` if parent has active session, else `clean` |
| `"context"` | Child re-uses parent's sandbox (shared volume, lighter-weight) |
| `"clean"` | Fresh sandbox, no volume sharing with parent |

### `auto` (Default)

The runtime inspects the parent interpreter state:
- If the parent has an active Daytona session with a mounted volume, the child
  is created in `context` mode (shared volume, lower overhead).
- If the parent has no active session (e.g., host-side delegation without a
  running interpreter), the child is created in `clean` mode.

### `context`

- Child shares the parent's durable volume (`/home/daytona/memory`).
- Lower startup cost (no volume provisioning).
- Useful when child needs access to files the parent has prepared.
- Risk: child can write to shared volume — use for read-heavy sub-tasks.

### `clean`

- Fresh sandbox with its own isolated volume.
- No file sharing with parent.
- Higher startup cost but complete isolation.
- Use for untrusted or write-heavy sub-tasks.

## ChildForkFallback Enum

Controls behavior when forking (context-mode creation) fails.

| Fallback | Behavior |
| --- | --- |
| `"clean"` | If forking fails, create a new clean sandbox instead |
| `"fail"` | If forking fails, raise an error immediately |

Default is `"clean"` — graceful degradation to full isolation.

## Building a Child Interpreter

```python
child = interpreter.build_delegate_child(
    remaining_llm_budget=6,
    isolation_mode="auto",       # or "context" / "clean"
    fork_fallback="clean",       # or "fail"
)
```

The child interpreter:
- Inherits the parent's `repo_url` and `repo_ref`.
- Gets its own `max_llm_calls` from the budget lease.
- Is configured with the specified isolation mode.
- Records metadata about its creation for observability.

## Metadata Recording

```python
record_child_isolation_metadata(
    child=child,
    timeout=180,
    retries=1,
    sandbox_id="ws-abc-123",
)
```

Records:
- Isolation mode selected (and whether auto-resolved to context or clean).
- Timeout configuration.
- Retry count.
- Sandbox ID for correlation with Daytona workspace logs.

## Cleanup

Child interpreters must always be shut down in a `finally` block:

```python
child = interpreter.build_delegate_child(remaining_llm_budget=6)
child.start()
try:
    result = child.execute(code)
finally:
    child.shutdown()  # Sandbox destroyed, resources released
```

`child.shutdown()`:
- Destroys the Daytona workspace.
- Releases any volume mounts.
- Deregisters the child from the parent's active-children set.

## Recursion Depth

- `max_recursion_depth` (default: 2) limits nesting.
- A child at depth N can only create grandchildren if `N < max_recursion_depth`.
- Depth is tracked via `_current_depth` on the interpreter.
- Exceeding the limit raises `MaxRecursionDepthError`.

```python
# Parent (depth 0) -> Child (depth 1) -> Grandchild (depth 2) -> BLOCKED
```

This prevents runaway delegation chains and ensures bounded resource usage.
