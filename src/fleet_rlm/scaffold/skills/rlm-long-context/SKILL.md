---
name: rlm-long-context
description: (EXPERIMENTAL) Research implementation for RLM long-context processing using standalone Python scripts. For production use, prefer the rlm skill which uses the fleet-rlm package with Modal sandboxes. This skill is for experimentation, evaluation, and alternative implementation patterns.
---

# RLM Long-Context Processing (Experimental)

> **For production use**, prefer the **`rlm` skill** which uses the fleet-rlm package with Modal cloud sandboxes.
>
> **Use this skill** for evaluating alternative RLM strategies, researching optimization techniques, or comparing approaches.

## Scripts & References

| Resource                                                               | Purpose                                                                                    |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `scripts/orchestrate.py`                                               | Main orchestrator with all optimizations                                                   |
| `scripts/rank_chunks.py`                                               | Query-guided chunk selection (5-10x speedup)                                               |
| `scripts/semantic_chunk.py`                                            | Content-aware chunking by boundaries                                                       |
| `scripts/cache_manager.py`                                             | Result caching for repeated queries                                                        |
| `scripts/codebase_concat.py`                                           | Concatenate codebase files for processing                                                  |
| [references/advanced-techniques.md](references/advanced-techniques.md) | Query-guided selection, semantic chunking, adaptive sizing, map-reduce, streaming, caching |
| [references/codebase-processing.md](references/codebase-processing.md) | Whole-codebase analysis: concatenation, code chunking, file selection, query types         |

## Architecture

```
Main Agent (Orchestrator)
  +-- Query-Guided Selection (filter chunks by relevance)
  +-- Semantic Chunking (content-aware boundaries)
  +-- Parallel subagent delegation with caching
       |
  +----+----+
  v    v    v
 Chunk A  Chunk B  Chunk C (skipped if low relevance)
  |    |
  v    v
 rlm-subcall    rlm-subcall
  |    |
  +----+----> Result Caching + Streaming (early exit)
       v
  Hierarchical Merge (>1M tokens: chunk > summary > synthesis)
       v
  Final Answer (Main Agent)
```

## Core Workflow

### 1. Prepare Content with Scripts

```bash
# Rank chunks by query relevance (skip irrelevant ones)
python3 .claude/skills/rlm-long-context/scripts/rank_chunks.py \
  --query "find all timeout errors" --top-k 10

# Chunk by semantic boundaries (auto-detects content type)
python3 .claude/skills/rlm-long-context/scripts/semantic_chunk.py \
  --state .claude/rlm_state/state.pkl

# For codebases: concatenate files first
python3 .claude/skills/rlm-long-context/scripts/codebase_concat.py \
  --root ./src --output codebase.txt
```

### 2. Choose Chunking Strategy

| Strategy                    | When to Use                                     | Tool                                                            |
| --------------------------- | ----------------------------------------------- | --------------------------------------------------------------- |
| **Query-guided selection**  | Query has clear keywords                        | `scripts/rank_chunks.py`                                        |
| **Semantic chunking**       | Structured content (logs, markdown, JSON, code) | `scripts/semantic_chunk.py`                                     |
| **Adaptive sizing**         | Mixed-density content                           | See [advanced-techniques.md](references/advanced-techniques.md) |
| **Fixed-size with overlap** | Unstructured text                               | `scripts/orchestrate.py`                                        |

For detailed code examples, see [references/advanced-techniques.md](references/advanced-techniques.md).
For whole-codebase analysis, see [references/codebase-processing.md](references/codebase-processing.md).

### 3. Delegate to Subagents

For each selected chunk, invoke `rlm-subcall`:

```yaml
subagent: rlm-subcall
input:
  query: "Find all ERROR entries and their timestamps"
  chunk_path: ".claude/rlm_state/chunks/chunk_001.txt"
  chunk_id: "chunk_001"
  format: "json"
```

Expected output:

```json
{
  "chunk_id": "chunk_001",
  "relevant": [{ "point": "...", "evidence": "...", "confidence": "high" }],
  "missing": ["what could not be determined"],
  "suggested_queries": ["follow-up questions"]
}
```

### 4. Collect & Synthesize

Merge results, identify cross-chunk patterns, produce final answer.
For files > 1M tokens: use hierarchical map-reduce (see [advanced-techniques.md](references/advanced-techniques.md)).

## NEVER List

- **NEVER paste entire chunks into main chat context** — causes context overflow. Quote only findings (<1KB).
- **NEVER spawn subagents from subagents** — exponential resource consumption. Orchestration stays in main agent.
- **NEVER split content mid-logical-unit** — use semantic chunking with boundary detection.
- **NEVER skip result validation before caching** — corrupted results poison the cache.
- **NEVER use fixed-size chunks without overlap for structured data** — 10% overlap minimum or semantic boundaries.
- **NEVER process all chunks when query is specific** — use query-guided selection first; process only top-K.

## Limitations

- Subagent outputs accumulate in main context: monitor total size
- Parallel execution limited by available subagent workers
- File must fit in memory (typically 2-4GB)
- No automatic retry on subagent failure (implement manually)
