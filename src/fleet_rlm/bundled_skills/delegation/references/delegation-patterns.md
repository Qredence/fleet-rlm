# Delegation Patterns and Bounds

## Budget and Batch Bounds

| Parameter | Valid Range | Enforcement |
| --- | --- | --- |
| `len(subtasks)` in `spawn_agents` | `1..8` | Rejected by `spawn_agents` and `build_child_specs` before starting children |
| `max_iters` per child | `1..8` (`int`, not `bool`) | Rejected before allocating any child sandbox |
| `max_llm_calls` per child | `1..25` (`int`, not `bool`) | Rejected before allocating any child sandbox |
| `jev_decide_batched` / `llm_query_batched` | `1..100` items | Preferred before spawning full child sandboxes when isolation is unnecessary |

All child executions share the parent Turn's remaining deadline and budget ledger.

## Fan-Out / Fan-In JSON Schemas

Instruct each child subtask to write a deterministic JSON file with explicit
keys under `artifacts/` (for example, `artifacts/region_north.json`):

```json
{
  "slice": "north",
  "status": "ok",
  "metrics": {"revenue": 12450.5, "count": 42}
}
```

Promoted child outputs appear under `artifacts/<agent-id>/...` and are returned
in `result["artifacts"]` as `{"path": "artifacts/<agent-id>/...", "sha256": "...", "agent_id": "..."}` entries.
Use `collect_child_outputs(results, parse_json_artifacts=True)` to parse promoted
JSON files cleanly instead of parsing unstructured `findings` prose.

## Partial Child Failure Recovery

- `spawn_agents` isolates ordinary child failures and returns
  `{"findings": "", "error": "<ExceptionType>: ...", "artifacts": []}` in that
  slot without promoting unverified files.
- Check `summary["all_succeeded"]` and `summary["errors"]` from
  `collect_child_outputs(results)`. Retry only the failed slices (or adjust
  `input_paths` / `max_iters`) rather than re-running successful children.
- Keep `artifacts/` dependencies explicit: pass any prior-turn or earlier-child
  file via `input_paths`, since `artifacts/` starts empty inside a child unless
  those paths are explicitly listed.
