---
name: dspy-rlm
description: Use when analyzing, explaining, or implementing dspy.RLM (Recursive Language Model / REPL code agent). Not for RAG or dspy.Retrieve.
compatibility: Requires Fleet RLM with a Python interpreter. Durable long reports use Daytona Session Workspace.
metadata:
  version: "1.0.0"
allowed-tools: read_skill_resource
---

# DSPy RLM (Recursive Language Model)

Load this optional Skill only when the request is specifically about explaining
or implementing `dspy.RLM`; ordinary RLM Turns do not need it.

1. `load_skill(...)` returns a dictionary; read its Skill body from
   `load_result["skill_markdown"]`. Read `references/rlm-contract.md` with
   `read_skill_resource` when you need constructor limits, built-ins, or Fleet
   mapping. That call also returns a dictionary: extract
   `contract = resource_result["content"]` instead of slicing the result.
2. Treat `dspy.RLM` as a **Recursive Language Model**: a sandboxed REPL code agent. Never call it a Retrieval Language Model or redefine it as RAG / `dspy.Retrieve` / ReAct.
3. Prefer Python standard-library computation, parsing, search, and aggregation
   for deterministic work. Use `llm_query` for one bounded semantic judgment and
   `llm_query_batched` for multiple independent semantic judgments with
   self-contained prompts.
4. Ground any semantic prompts in the extracted contract string. Do not invent
   DSPy RLM APIs from training priors.
5. For long writeups when Session Workspace is available, follow report-builder
   / workspace-files: write the full report durably, then issue exactly one
   typed `SUBMIT` with every active Signature output.
6. For nontrivial deterministic work, verify in a later iteration using an
   independent invariant, known reference, higher-precision stability, or a
   genuinely independent formulation before submitting.

Authority: pinned DSPy 3.3.0b1 and https://dspy.ai/api/modules/RLM/ — not Daytona docs as DSPy authority.
