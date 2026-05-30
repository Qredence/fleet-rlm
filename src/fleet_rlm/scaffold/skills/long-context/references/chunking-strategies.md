# Chunking Strategies

Use this reference only when the task needs manual chunk files. Prefer the
runtime's variable-mode path when a Daytona interpreter can inspect the large
input directly.

## Semantic Chunking

Use semantic chunking when source boundaries matter: markdown sections, log
entries, JSON containers, or Python definitions.

```bash
uv run python src/fleet_rlm/scaffold/skills/long-context/scripts/semantic_chunk.py \
  --state .codex/rlm_state/state.pkl \
  --type markdown \
  --max-size 8000 \
  --output .codex/rlm_state/chunks
```

Supported `--type` values: `auto`, `markdown`, `log`, `json`, `python`, `text`.

## Query Ranking

Use ranking before delegation when the user asks about specific identifiers,
subsystems, errors, or concepts.

```bash
uv run python src/fleet_rlm/scaffold/skills/long-context/scripts/rank_chunks.py \
  --state .codex/rlm_state/state.pkl \
  --query "authentication token refresh" \
  --top-k 8 \
  --chunks-dir .codex/rlm_state/chunks
```

`rank_chunks.py` emits chunk paths that match the semantic chunk naming pattern.
Pass those paths, the query, and the source file context into the `delegation`
skill for child RLM work.

## Codebase Inputs

For codebases, avoid concatenating dependency folders. Build a bounded snapshot
from relevant paths first, then chunk that snapshot while preserving `FILE:
<path>` markers in each chunk.
