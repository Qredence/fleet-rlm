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

If you need to process the entire document to find all occurrences of something (e.g., API endpoints), straightforward reading is slow. RLM uses batching.

### Command

```bash
uv run fleet-rlm run-api-endpoints --docs-path rlm_content/dspy-knowledge/dspy-doc.txt
```

This command splits the document and runs parallel queries to extract endpoints defined in different sections simultaneously.

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
