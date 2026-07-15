---
name: long-context
description: "Process documents and codebases exceeding a single context window using dspy.RLM variable mode in the Daytona REPL."
---

# Long-context processing (dspy.RLM)

Official references:

- https://dspy.ai/api/modules/RLM/
- https://dspy.ai/diving-deeper/rlm/

## Core pattern (variable space vs token space)

`dspy.RLM` stores large inputs as **REPL variables**. The model should see metadata (name, type, length, preview) and explore with Python:

1. Peek: `print(text[:2000])`, `print(len(text))`.
2. Locate: slices, `re`, indexes — not full dumps into the action prompt.
3. Reason on excerpts: `llm_query(snippet)` or `llm_query_batched([...])`.
4. Finish: `SUBMIT(answer=...)`.

When attachments are bound, use host `read_attachment(attachment_id)` for file bodies instead of inventing host paths.

## Clean turn context

`RLMExecutionContext` carries validated request/history, native RLM Options, the Turn Timeout deadline, prepared Attachments, authorized capabilities, and one Interpreter. There is **no** live auto-router (`large_context_rlm` / EscalatingFleet). Prefer this skill whenever the user paste or attachment is large.

## Optional offline pre-chunking

Host-side scripts (not required inside the sandbox):

- `scripts/semantic_chunk.py` — split by structure
- `scripts/rank_chunks.py` — rank chunks against a query

See `references/chunking-strategies.md`. Chunking complements RLM; it does not replace REPL inspection.

## Guardrails

- Do not paste whole documents into the action prompt or final answer.
- Do not call `llm_query` on an entire large variable; slice first.
- Respect `max_llm_calls` and `max_output_chars`; print summaries, not raw dumps.
- Load deeper skill text via host `load_skill(skill_id)` using SkillCard ids — not volume name lookup.

## Exact quote retrieval

When the user asks for a verbatim quote:

- Locate with Python search, slice the span, return **one** exact quote in `SUBMIT(answer=...)`.
- Do not paraphrase or invent speaker labels without evidence.
