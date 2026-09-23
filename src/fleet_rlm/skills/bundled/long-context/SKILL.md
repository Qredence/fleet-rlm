---
name: long-context
description: Discover public sources and analyze large documents, transcripts, code, or datasets with sandbox Python.
compatibility: Requires Fleet RLM variable mode with a Python interpreter.
metadata:
  version: "2.2.0"
  affordances:
    - sandbox.search
    - llm_query_batched
    - workspace.files
allowed-tools: read_skill_resource

resources:
  - path: scripts/semantic_chunk.py
    media_type: text/x-python
  - path: scripts/rank_chunks.py
    media_type: text/x-python
  - path: references/chunking-strategies.md
    media_type: text/markdown

---

# Long-context analysis

Keep large inputs in variable space. Inputs may come from the user query, committed Session History, Skill resources, Attachments, a public URL, or Session Workspace. Explore them with bounded Python operations, send only relevant excerpts to sub-model calls, and verify every reported fact against the original variable before submitting.

## Analyze

1. Inspect variable names, types, lengths, and small previews. Keep reusable large data in variables or files; do not print a whole large value. If URLs must be discovered, install `ddgs==9.16.0` with the active interpreter and search in the REPL:

   ```python
   import importlib, site, subprocess, sys

   subprocess.run([sys.executable, "-m", "pip", "install", "ddgs==9.16.0"], check=True, timeout=120, capture_output=True)
   site.addsitedir(site.getusersitepackages())
   importlib.invalidate_caches()
   from ddgs import DDGS

   results = DDGS(timeout=10).text(query, max_results=5)
   ```

   Keep only selected titles and URLs in the REPL output. Download selected URLs with Python in the Sandbox to `/workspace/sources`; read each file in bounded pages. Record its URL, retrieval time, path, and SHA-256 in a small sidecar file and, when available, record the path and checksum in the active task.
2. Locate candidate regions with deterministic searches, indexes, regular expressions, or bounded slices.
3. Choose the task's coverage rule. For a sparse question, search selected regions and expand when evidence calls for it. For exhaustive extraction, process every required partition, record which were processed, and keep a list of failed or unprocessed partitions. Ranking may prioritize reading order but cannot exclude unseen required evidence. For dependent questions, resolve prerequisites before comparing or parallelizing downstream findings.
4. Call `llm_query` or `llm_query_batched` only on self-contained excerpts that include the question and their source offsets or source identifiers, unless the request already specifies the exact prompt strings: then pass those strings unchanged and in the given order; do not add offsets, paraphrase, or substitute different wording. Use `rlm_query` only for the rare selected subproblem that needs a fresh iterative Python investigation; it is not the normal route for extraction, counting, parsing, aggregation, or independent excerpts.
5. Validate semantic outputs against their source slices, retry only invalid items when justified, and reduce verified structured results in Python. Reconcile definitions, scope, dates, exceptions, amendments, cross-references, and precedence rules relevant to the task.
6. Recover complete operative language from the original variable, then re-slice it to verify quotes, offsets, qualifiers, and conclusions.
7. Call `SUBMIT(...)` once with exactly the requested output fields. If the evidence is absent, ambiguous, or insufficient, state that instead of forcing the requested count. Do not `SUBMIT` an entire large `llm_query` or `llm_query_batched` blob; keep declared answers within the Turn output character budget.

Treat sub-model output as a candidate, never as source evidence. Respect the Turn's call and output budgets; reduce excerpts before making another call. Paging, indexes, and chunk files are useful when input size or reuse justifies them; keep source identifiers, revisions, and offsets with derived records. If an exhaustive scan stops early, report incomplete coverage instead of claiming a complete result.

## Exact retrieval

For an exact quote, find the text with Python, retain its source offset, and compare the final excerpt byte-for-byte or character-for-character with the original input. If the evidence is absent or ambiguous, report that instead of inventing a quote or speaker.
