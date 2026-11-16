---
name: delegation
description: Split independent work across child sandboxes and combine promoted artifacts. Use when subtasks need isolated files or code execution.
---

# Delegation with child agents

Use local Python for deterministic work, batched Jev for fixed-label decisions,
batched queries for free-form reading, or `dspy.Predict` for structured synthesis.
Reserve `spawn_agents` / `spawn_agent` for independent parts needing isolated
sandboxes, file scopes, or parallel code execution.

## Workflow

1. Load the helper module and choose a mechanism matching the task:

   ```python
   del_tools = load_skill_module("delegation")
   plan = del_tools.choose_mechanism(item_count=3, needs_isolated_files_or_code_exec=True)
   print(plan["mechanism"], plan["batches_needed"])
   ```

2. Validate prompts and `input_paths` before launching any child (`max_iters` in
   `1..8`, `max_llm_calls` in `1..25`, `<= 8` subtasks per `spawn_agents` call).
   Every child receives the Session's Skills under `skills/`, while
   `artifacts/` starts empty inside a child unless specific `artifacts/...`
   paths are requested in `input_paths`:

   ```python
   spec = del_tools.build_child_specs(
       [
           {"prompt": "For north, compute revenue. Write artifacts/north.json.", "input_paths": ["files/sales.csv"]},
           {"prompt": "For south, compute revenue. Write artifacts/south.json.", "input_paths": ["files/sales.csv"]},
       ]
   )
   results = spawn_agents(subtasks=spec["subtasks"], input_paths=spec["input_paths"], max_iters=8, max_llm_calls=12)
   ```

3. Combine promoted outputs (`artifacts/<agent-id>/...`) with structured parsing
   and inspect `errors` for partial-failure recovery:

   ```python
   collected = del_tools.collect_child_outputs(results, parse_json_artifacts=True)
   print(collected["succeeded_count"], collected["failed_count"], collected["parsed_json"])
   ```

4. Read `read_skill_reference("delegation", "references/delegation-patterns.md")`
   for JSON schemas, partial child failure recovery, and budget bounds.

5. Write combined deliverables with `workspace.write_artifact(...)`, print the
   final figures, and `SUBMIT` from variables.
