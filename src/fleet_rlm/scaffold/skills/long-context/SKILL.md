---
name: long-context
description: "Process documents and codebases exceeding a single context window. Use when handling large files, designing chunking strategies, or orchestrating multi-chunk synthesis via variable-mode execution or hierarchical map-reduce."
---

# Long-Context Processing

## Decision Tree

1. **Input fits normal context**: answer directly; do not chunk.
2. **Input is large and a Daytona interpreter is available**: prefer variable-mode so the model can inspect content through code.
3. **Input is large and needs focused extraction**: create semantic chunks, rank them against the query, then delegate only the relevant chunks.
4. **Input is codebase-scale**: preserve file paths in every chunk and delegate by file or subsystem, not by anonymous text windows.

## Included Helpers

- `scripts/semantic_chunk.py`: split markdown, logs, JSON, Python, or generic text into bounded chunks.
- `scripts/rank_chunks.py`: score chunk ranges against query keywords and emit the highest-value chunk paths.
- `references/chunking-strategies.md`: compact command reference for the two helper scripts.

## Workflow

1. Store the large input in the local RLM state file (`.codex/rlm_state/state.pkl` or an explicit `--state` path).
2. Run semantic chunking with the content type closest to the input.
3. Rank chunks against the user query before delegating.
4. Pass each selected chunk to `delegation` with file path, chunk id, and query context.
5. Merge child results in the parent; quote findings, not full chunks.

## Guardrails

- Do not paste whole chunks into the main chat context.
- Do not spawn child RLMs from child RLMs.
- Do not split structured content with blind fixed-size windows when semantic boundaries are available.
- Do not process every chunk when the query has clear keywords or identifiers.
