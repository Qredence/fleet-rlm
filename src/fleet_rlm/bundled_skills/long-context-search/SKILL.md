---
name: long-context-search
description: Find and cite evidence in large workspace text files, logs, or transcripts. Use when bounded line or overlapping chunk searches are needed.
---

# Long-context search

Use this when a selected `context` dataset or workspace file is too large to
print, or when evidence is scattered across a large log, transcript, or document.

## Steps

1. Load the search module and probe file size/line bounds before reading:

   ```python
   lcs = load_skill_module("long-context-search")
   path = context.path if "context" in globals() and hasattr(context, "path") else "/workspace/files/app.log"
   print(lcs.probe_files([path]))
   ```

2. Narrow candidates in Python first (auto-switches to overlapping chunks on
   >1 MB lines or minified files):

   ```python
   res = lcs.search_windows(path, r"payment\s+failed|timeout", context_lines=4, max_hits=50)
   print(res["mode"], res["hit_count"], res["truncated"])
   ```

   `hit_count` counts returned matching lines or chunk matches, not necessarily
   all occurrences. If `truncated` is true, do not report it as a corpus total.
   Overlapping chunks detect boundary matches up to the configured overlap;
   use enough overlap for the evidence sought. Cite character offsets in chunk mode.
   Use `lcs.read_line_range(path, start_line, end_line)` to expand specific
   passages around a line-mode hit.

3. For semantic classification over matched windows, build bounded batches
   (<= 100 items/call) and call `jev_decide_batched` or `llm_query_batched`:

   ```python
   batches = lcs.build_jev_batches(res["hits"], "Does this passage report a failed payment?")
   matched = []
   for batch in batches:
       decisions = jev_decide_batched(batch["items"], batch["question"])
       for row, meta in zip(decisions, batch["metadata"]):
           if row["decision"] is True:
               matched.append(meta)
   print(len(matched), matched[:10])
   ```

4. Read `read_skill_reference("long-context-search", "references/batch-reading-patterns.md")`
   for variable-space bounds and batching guidance.

5. Print final counts and cited `(line_number)` / offset provenance, then `SUBMIT`
   from those variables.
