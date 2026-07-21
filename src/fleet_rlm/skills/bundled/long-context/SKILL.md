---
name: long-context
description: Use when a document, codebase, transcript, or dataset is too large to inspect reliably in one model context, especially for exact retrieval or evidence synthesis.
compatibility: Requires Fleet RLM variable mode with a Python interpreter.
metadata:
  version: "2.0.0"
allowed-tools: read_skill_resource
---

# Long-context analysis

Keep large inputs in variable space. Explore them with bounded Python operations, send only relevant excerpts to sub-model calls, and verify every reported fact against the original variable before submitting.

## Analyze

1. Inspect variable names, types, lengths, and small previews. Do not print a whole large value.
2. Locate candidate regions with deterministic searches, indexes, regular expressions, or bounded slices.
3. When the relevant regions are unknown, scan every bounded chunk for structured candidates before reducing them. Query ranking may prioritize reading order, but it must not exclude unseen evidence.
4. Call `llm_query` or `llm_query_batched` only on self-contained excerpts that include the question and their source offsets.
5. Reconcile candidates against definitions, scope, dates, exceptions, amendments, cross-references, and precedence rules relevant to the task.
6. Recover complete operative language from the original variable, then re-slice it to verify quotes, offsets, qualifiers, and conclusions.
7. Call `SUBMIT(...)` once with exactly the requested output fields. If the evidence is absent, ambiguous, or insufficient, state that instead of forcing the requested count.

Treat sub-model output as a candidate, never as source evidence. Respect the Turn's call and output budgets; reduce excerpts before making another call.

## Optional deterministic helpers

Read these resources only when structural chunk files or query ranking will help:

- `scripts/semantic_chunk.py` splits one explicit UTF-8 input file into deterministic text chunks.
- `scripts/rank_chunks.py` ranks an explicit directory of text chunks using lexical query matches.
- `references/chunking-strategies.md` documents their arguments and when chunking is useful.

The scripts have no hidden state. Supply every input and output path explicitly and use a new or empty output directory; do not create pickle state or rely on a previous Run. Manifest offsets are Python character offsets in the helper's decoded input file, not byte offsets and not offsets into another variable until re-aligned and verified.

## Exact retrieval

For an exact quote, find the text with Python, retain its source offset, and compare the final excerpt byte-for-byte or character-for-character with the original input. If the evidence is absent or ambiguous, report that instead of inventing a quote or speaker.
