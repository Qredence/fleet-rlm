---
name: long-context-search
description: Find, count, or summarize evidence in a large selected text, log, or document file that is too big to read at once, using streaming lines or chunks, keyword search, and (when needed) batched sub-LLM reading with cited line numbers.
---

# Long-context search

Use this when a selected dataset is too large to print or the answer depends on
scattered passages in that file, such as a log, transcript, or long document. For
other workspace files, inspect `workspace.tree()` and read the relevant files in
bounded pieces; the `context` methods apply only when a dataset was selected.

## Steps

1. Measure first: `workspace.stat(path)` gives the size; `workspace.tree()` lists the files.
2. A selected dataset arrives as `context`. Use `context.preview()`,
   `context.lines()`, `context.chunks(chunk_bytes)` and `context.search(query, limit)`.
   `search` returns `(line_number, line)` pairs.
3. Narrow with code before using models. Use exact keyword or regex filters,
   and do counts and group-bys in Python. Print the counts.
4. For semantic judgments over many passages, batch them:

   ```python
   prompts = [f"Does this line report a failed payment? Answer yes or no.\n{line}" for _, line in hits]
   answers = llm_query_batched(prompts)
   ```

   For a fixed set of labels per item, `jev_decide_batched(items, question, choices)`
   returns one typed decision per item.
5. Keep evidence: store `(line_number, excerpt)` for every claim and cite the
   line numbers in the answer.
6. Count and aggregate in code from the decisions, print the totals, then
   SUBMIT from those variables.

## Pitfalls

- Do not print a whole large file; it is truncated and wastes iterations.
- `lines()` raises on lines over 1 MB; use `chunks()` for binary or minified files.
- Sub-LLM calls share the Turn budget; filter first, then ask.
