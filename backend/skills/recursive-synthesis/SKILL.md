---
name: recursive-synthesis
description: Use when a complex workspace task needs independent subtasks, semantic map-reduce across files, or structured extraction across many chunks.
---

Use bounded `llm_query_batched` for text chunks; retry or report failed items. Use `dspy.Predict` for single-step structured extraction and `spawn_agent(task)` for independent multi-step work. Child interpreters share workspace files and the call budget; assign distinct artifact paths. Verify child results and merge artifacts before `SUBMIT`.
