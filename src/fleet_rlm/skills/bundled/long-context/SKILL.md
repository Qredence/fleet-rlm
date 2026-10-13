---
name: long-context
description: Analyze large documents, transcripts, code, or acquired source text with sandbox Python.
compatibility: Requires Fleet RLM variable mode with a Python interpreter.
metadata:
  version: "2.6.0"
  affordances:
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

1. Inspect variable names, types, lengths, and small previews. Keep reusable large data in variables or files; do not print a whole large value. For public source discovery and page access, load the advertised `web-research` Skill when permitted by the current selection. Exact selections do not implicitly load other Skills; select both Skills when both workflows are needed. Analyze already acquired Attachments, Workspace files, and captured web evidence directly.
2. Locate candidate regions with deterministic searches, indexes, regular expressions, or bounded slices. For labelled policy documents, inspect section headings and authority markers before ranking broad keywords: repeated training examples may dominate keyword hits. The `semantic_chunk.py` helper supports `[SECTION-ID] Heading` blocks with `sectioned` or `auto`; retain the complete block and original offsets so a cross-reference in prose is not mistaken for a heading.
3. Choose the task's coverage rule. For a sparse question, search selected regions and expand when evidence calls for it. For exhaustive extraction, process every required partition, record which were processed, and keep a list of failed or unprocessed partitions. Ranking may prioritize reading order but cannot exclude unseen required evidence. For dependent questions, resolve prerequisites before comparing or parallelizing downstream findings.
4. Call `llm_query` or `llm_query_batched` only on self-contained excerpts that include the question and their source offsets or source identifiers, unless the request already specifies the exact prompt strings: then pass those strings unchanged and in the given order; do not add offsets, paraphrase, or substitute different wording. Use `rlm_query` only for the rare selected subproblem that needs a fresh iterative Python investigation; it is not the normal route for extraction, counting, parsing, aggregation, or independent excerpts.
5. Validate semantic outputs against their source slices, retry only invalid items when justified, and reduce verified structured results in Python. Reconcile definitions, scope, dates, exceptions, amendments, cross-references, and precedence rules relevant to the task.
6. Recover complete operative language from the original variable, then re-slice it to verify quotes, offsets, qualifiers, and conclusions.
7. Call `SUBMIT(...)` once with exactly the requested output fields. If the evidence is absent, ambiguous, or insufficient, state that instead of forcing the requested count. Do not `SUBMIT` an entire large `llm_query` or `llm_query_batched` blob; keep declared answers within the Turn output character budget.

Treat sub-model output as a candidate, never as source evidence. Respect the Turn's call and output budgets; reduce excerpts before making another call. Budget the combined printed excerpts, not each excerpt separately; keep full blocks in variables and print a bounded index or selected complete clauses when many matches would truncate the interpreter output. Paging, indexes, and chunk files are useful when input size or reuse justifies them; keep source identifiers, revisions, and offsets with derived records. If an exhaustive scan stops early, report incomplete coverage instead of claiming a complete result.

## Strategy examples

- **Sparse:** search the likely sections, inspect the matching source slices, then expand to nearby or conflicting sections only when needed.
- **Exhaustive:** enumerate the required partitions, classify bounded source slices with `llm_query_batched`, validate each returned item in Python, and record `processed / required` coverage before aggregation.
- **Dependent:** resolve and verify the prerequisite first; only then inspect dependent records. If the prerequisite is unresolved, report that gap instead of parallelizing work that assumes an answer.

Use a full child RLM only when one selected subproblem needs its own repeated Python inspection. Pass authorized relative inputs and a small question; keep large sources and intermediate results in variables or files, and have the root verify child evidence before using it.

## Exact retrieval

For an exact quote, find the text with Python, retain its source offset, and compare the final excerpt byte-for-byte or character-for-character with the original input. If the evidence is absent or ambiguous, report that instead of inventing a quote or speaker.
