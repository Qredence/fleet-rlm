---
name: recursive-synthesis
description: Decompose complex multi-file or multi-step workspace problems using llm_query_batched, dspy.Predict, and spawn_agent.
---

Match recursion primitives to subtask complexity within the shared call budget:
1. `llm_query_batched(prompts)`: concurrent semantic map-reduce over text chunks in one REPL step.
2. `dspy.Predict` / `dspy.ChainOfThought`: structured single-step extraction in Python.
3. `spawn_agent(task)`: delegate isolated multi-step Python workspace subtasks (never instantiate nested `dspy.RLM` directly). Verify child results and artifacts before `SUBMIT`.
