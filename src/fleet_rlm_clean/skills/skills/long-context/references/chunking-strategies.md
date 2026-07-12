# Chunking strategies (offline)

Use only when the task needs manual chunk files on the host. Prefer RLM variable-mode inspection when a Daytona interpreter is available.

## Semantic chunking

```bash
uv run python src/fleet_rlm_clean/skills/skills/long-context/scripts/semantic_chunk.py \
  --state .codex/rlm_state/state.pkl \
  --type markdown \
  --max-size 8000 \
  --output .codex/rlm_state/chunks
```

Supported `--type` values: `auto`, `markdown`, `log`, `json`, `python`, `text`.

## Query ranking

```bash
uv run python src/fleet_rlm_clean/skills/skills/long-context/scripts/rank_chunks.py \
  --state .codex/rlm_state/state.pkl \
  --query "authentication token refresh" \
  --top-k 8 \
  --chunks-dir .codex/rlm_state/chunks
```

Feed top-ranked excerpts into `llm_query` / parent synthesis. Clean does not ship host `delegate_to_rlm` — keep map-reduce inside one RLM turn or sequential host turns.

## Codebase inputs

Avoid concatenating dependency folders. Build a bounded snapshot of relevant paths first; preserve `FILE: <path>` markers in each chunk.
