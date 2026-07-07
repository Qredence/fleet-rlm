---
name: long-context
description: "Process documents and codebases exceeding a single context window using canonical dspy.RLM variable mode in the Daytona REPL."
---

# Long-Context Processing (dspy.RLM)

Official references:

- https://dspy.ai/api/modules/RLM/
- https://dspy.ai/diving-deeper/rlm/

## Core pattern (variable space vs token space)

`dspy.RLM` stores large inputs as **REPL variables**. fleet-rlm passes staged workspace data as a `context` object with `context["document_text"]`, `context["manifest"]`, and `context["metadata"]`. The model sees only metadata (name, type, length, preview) and explores with Python:

1. `print(context["document_text"][:2000])` or `print(len(context["document_text"]))` to peek.
2. Use slices, `re`, and `context["manifest"]` to locate relevant sections.
3. Call `llm_query(snippet)` or `llm_query_batched([...])` on focused excerpts — never the full document.
4. Finish with `SUBMIT(response=...)`.

## fleet-rlm auto-routing

- `execution_mode=auto` routes to `large_context_rlm` when estimated context ≥ `FLEET_RLM_LARGE_CONTEXT_THRESHOLD` (default 32_000 chars).
- Staged workspace data arrives as `context["document_text"]`, `context["manifest"]`, and `context["metadata"]`.
- If `context["metadata"]["sandbox_staged_paths"]` is present, use those sandbox paths only. Do not open host paths from `context["manifest"]`.
- Optional `sub_rlm(text)` delegates to an isolated child sandbox for heavy map-reduce (see `delegation` skill).

## Optional pre-chunking

When semantic boundaries matter before delegation:

- `scripts/semantic_chunk.py` — split by structure (markdown, logs, Python, JSON).
- `scripts/rank_chunks.py` — rank chunks against the query.

Chunking complements `dspy.RLM`; it does not replace REPL inspection.

## Guardrails

- Do not paste whole documents into the action prompt or assistant reply.
- Do not call `llm_query` on an entire large variable; slice first.
- Respect `max_llm_calls` and `max_output_chars`; print summaries, not raw dumps.
- Load this skill from the volume with `load_skill("long-context")` when mounted at `/home/daytona/memory/`.

## Exact quote retrieval

When the user asks for a verbatim quote or speaker attribution:

- Return exactly one quote block in `SUBMIT` — not a numbered list of quotes.
- Locate the speaker in `context["document_text"]` with Python search, then slice the typographic quote span verbatim.
- Do not paraphrase, substitute heading text, or open host paths from `context["manifest"]` in the sandbox.
