---
name: delegation
description: Split a task into independent child agents with spawn_agents or spawn_agent, pass each child exactly the workspace files it needs (files/, skills/, or earlier artifacts/), stay within child limits, and combine their promoted artifacts.
---

# Delegation with child agents

Use this when a task has independent parts (for example "for each region...",
or three unrelated questions), or when one part needs a fresh, isolated
investigation.

## Calls

Use keyword arguments only:

```python
results = spawn_agents(
    subtasks=[
        "For region north, find the best-selling product by ok-revenue. Write artifacts/north.json with keys product, revenue.",
        "Count orders per status. Write artifacts/status.json mapping status to count.",
    ],
    input_paths=[["files/sales.csv"], ["files/sales.csv"]],
    max_iters=8,
    max_llm_calls=10,
)
for result in results:
    print(result.get("error"), result["findings"][:300], result["artifacts"])
```

- Limits per child: `max_iters` from 1 to 8, `max_llm_calls` from 1 to 25, and
  at most 8 subtasks per `spawn_agents` call. Values outside these ranges are
  rejected before any child starts.
- `input_paths` copies the listed files. Paths may be under `files/`,
  `skills/`, or `artifacts/` (a previous Turn's report, for example). Every
  child also receives the Session's Skills under `skills/`; it sees nothing
  else from the parent.
- A failed child returns `{findings: "", error, artifacts: []}`. Handle it and
  retry or work around it; do not assume success.

## Combining results

- Each child's output files are promoted to `artifacts/<agent-id>/...`; the
  paths are in `result["artifacts"]`. Read them with
  `workspace.read_text(path)` and `json.loads` rather than trusting findings
  text.
- Name the exact output file and its JSON keys in each subtask so you can
  parse them.
- Print the combined values, then write the summary (for example
  `workspace.write_artifact("summary.md", table)`) and SUBMIT from variables.

## When not to delegate

A single short computation is faster in your own sandbox. Delegate only for
independent parts or when isolation matters.
