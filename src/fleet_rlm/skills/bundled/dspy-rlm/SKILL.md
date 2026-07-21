---
name: dspy-rlm
description: Use when analyzing, explaining, or implementing dspy.RLM (Recursive Language Model / REPL code agent). Not for RAG or dspy.Retrieve.
compatibility: Requires Fleet RLM with a Python interpreter. Durable long reports use Daytona Session Workspace.
metadata:
  version: "1.0.0"
allowed-tools: read_skill_resource
---

# DSPy RLM (Recursive Language Model)

Load this Skill before explaining or implementing `dspy.RLM`.

1. `load_skill(...)` returns a dictionary; read its Skill body from
   `load_result["skill_markdown"]`. Read `references/rlm-contract.md` with
   `read_skill_resource` when you need constructor limits, built-ins, or Fleet
   mapping. That call also returns a dictionary: extract
   `contract = resource_result["content"]` instead of slicing the result.
2. Treat `dspy.RLM` as a **Recursive Language Model**: a sandboxed REPL code agent. Never call it a Retrieval Language Model or redefine it as RAG / `dspy.Retrieve` / ReAct.
3. Ground any `llm_query` or `llm_query_batched` prompts in the extracted
   contract string. Do not invent DSPy RLM APIs from training priors.
4. For long writeups when Session Workspace is available, follow report-builder / workspace-files: write the full report durably, then `SUBMIT` a short summary within the Turn output budget.

Authority: pinned DSPy 3.3.0b1 and https://dspy.ai/api/modules/RLM/ — not Daytona docs as DSPy authority.
