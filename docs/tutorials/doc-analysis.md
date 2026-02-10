# Tutorial: Document Analysis

RLM shines when processing long documents. This tutorial shows how to extract structured information from a documentation file.

## Prerequisites

Ensure you have the text file you want to analyze. The repo includes `rlm_content/dspy-knowledge/dspy-doc.txt` for testing.

## Scenario 1: Extraction (Architecture)

We want to extract specific technical details like "Modules" and "Optimizers" from the DSPy documentation.

### Command

```bash
uv run fleet-rlm run-architecture \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "Extract all modules and optimizers"
```

### Comparison: RLM vs. Standard RAG

- **Standard RAG**: Embeds chunks, retrieves top-k, and hopes the content is there.
- **RLM**: Reads the structure (headers), identifies relevant sections for "Modules" and "Optimizers", extracts _only_ those sections, and synthesizes the list. It is more exhaustive and accurate for "list all X" queries.

## Scenario 2: Batch Processing (API Endpoints)

If you need to process the entire document to find all occurrences of something (e.g., API endpoints), straightforward reading is slow. RLM uses signature-driven extraction in the sandbox loop.

### Command

```bash
uv run fleet-rlm run-api-endpoints --docs-path rlm_content/dspy-knowledge/dspy-doc.txt
```

This command runs structured endpoint extraction over the document using the `ExtractAPIEndpoints` signature and sandbox execution loop.

## Scenario 3: Pattern Finding (Error Analysis)

This runs a stateful analysis to find common error patterns mentioned in the text.

### Command

```bash
uv run fleet-rlm run-error-patterns --docs-path rlm_content/dspy-knowledge/dspy-doc.txt
```

## Scenario 4: Custom Tools

RLM agents can use custom tools defined in `src/fleet_rlm/tools.py`. For example, a regex tool can be safer and faster than LLM pattern matching for strict formats.

### Command

```bash
uv run fleet-rlm run-custom-tool \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --chars 5000
```

This uses `regex_extract` inside the sandbox to find matches before processing them.

## Scenario 5: Long-Context Analysis

For very large documents that exceed typical context window limits, the `run-long-context` command leverages sandbox-side helpers to let the RLM explore the document programmatically.

### Analyze Mode

The RLM uses `peek`, `grep`, and `chunk_by_headers` to navigate the document, calls `llm_query` when semantic sub-analysis is needed, and synthesizes findings.

```bash
uv run fleet-rlm run-long-context \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "What are the main design decisions?" \
    --mode analyze
```

### Summarize Mode

The RLM chunks the document, queries relevant chunks with the given focus topic, and merges per-chunk summaries into a coherent whole.

```bash
uv run fleet-rlm run-long-context \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "DSPy optimizers" \
    --mode summarize
```

### How It Works

Under the hood the RLM has access to these sandbox-side helpers:

- **`peek(text, start=0, length=2000)`** — Inspect a slice of the document.
- **`grep(text, pattern, context=0)`** — Case-insensitive line search.
- **`chunk_by_size(text, size=200_000, overlap=0)`** — Fixed-size chunking.
- **`chunk_by_headers(text, pattern=r"^#{1,3} ", flags=re.MULTILINE)`** — Split at markdown headers.
- **`add_buffer` / `get_buffer` / `clear_buffer`** — Accumulate findings across iterations.
- **`save_to_volume` / `load_from_volume`** — Persist results across runs.

The Planner LLM writes Python code that calls these helpers, inspects the results, optionally delegates to sub-LLMs (`llm_query` / `llm_query_batched`), and eventually calls `SUBMIT()` with the structured output.
