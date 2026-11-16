# Batch Reading Patterns (DSPy 3.5.0)

## Variable-Space vs Token-Space

- Keep large logs, transcripts, and documents in Python variables and `/workspace`
  files rather than printing full files into model token-space.
- Probe first with `probe_files([path])` (or `workspace.stat(path)`) so you choose
  line-based vs chunk-based streaming before touching content.
- `WorkspaceDatasetView.lines()` raises `ValueError` on lines exceeding 1 MB;
  `search_windows` detects >1 MB lines and single-line minified files
  automatically and switches to overlapping `chunk_size`/`chunk_overlap` windows.

## `jev_decide_batched` vs `llm_query_batched`

| Helper | Output Type | Batch Limit | When to Use |
| --- | --- | --- | --- |
| `jev_decide_batched(items, question, choices=None)` | Typed boolean (`Noul`) or `Choice` label + probability/confidence | 1–100 items per call | Fixed-label classification, relevance filtering, yes/no event triage |
| `llm_query_batched(prompts)` | Free-form text strings | 1–100 prompts per call | Summarization, extracting open-ended quotes, structured reasoning over windows |

Filter candidate passages in Python (`search_windows`) *before* calling either
batch LM tool: both share the Turn's sub-LM budget.

## Line Provenance Citations

- Use `build_jev_batches(hits, question)` so every item retains its
  `[lines start-end | hit line N]` or `[chunk K | offsets A-B]` header and
  parallel `metadata` list.
- Zip decisions back with `batch["metadata"]`, aggregate counts in Python
  variables, print the totals and cited line ranges, and `SUBMIT` from variables.
