# RLM Long-Context Scripts

Helper scripts for the RLM (Recursive Language Model) long-context processing workflow.

## Scripts

### 1. `rank_chunks.py`

Query-guided chunk selection - ranks chunks by relevance before processing.

```bash
# Rank all chunks by query relevance
python3 scripts/rank_chunks.py --query "find all timeout errors"

# Get top 10 most relevant chunks
python3 scripts/rank_chunks.py --query "find all timeout errors" --top-k 10

# Save chunk list for processing
python3 scripts/rank_chunks.py --query "find errors" --top-k 5 -o relevant_chunks.txt
```

**Benefits:**
- 5-10x speedup by skipping irrelevant chunks
- Prioritizes high-value content first
- Enables early exit when sufficient evidence found

### 2. `semantic_chunk.py`

Content-aware chunking using semantic boundaries.

```bash
# Auto-detect content type and chunk semantically
python3 scripts/semantic_chunk.py --state .claude/rlm_state/state.pkl

# Force specific content type
python3 scripts/semantic_chunk.py --type markdown
python3 scripts/semantic_chunk.py --type log
python3 scripts/semantic_chunk.py --type json
python3 scripts/semantic_chunk.py --type python

# Custom output directory
python3 scripts/semantic_chunk.py -o ./my_chunks --max-size 150000
```

**Supported Types:**
| Type | Boundary Pattern |
|------|-----------------|
| `markdown` | Headers (`#`, `##`, etc.) |
| `log` | Timestamps (ISO 8601, syslog) |
| `json` | Top-level objects/arrays |
| `python` | Function/class definitions |
| `text` | Fixed size (fallback) |

**Benefits:**
- Preserves context at boundaries
- No "split mid-sentence" issues
- Better subagent comprehension

### 3. `cache_manager.py`

Cache subagent results to avoid re-processing.

```bash
# Check if result is cached
python3 scripts/cache_manager.py get --chunk chunk_0001.txt --query "find errors"

# Cache a result
python3 scripts/cache_manager.py set --chunk chunk_0001.txt --query "find errors" \
    --result '{"findings": [...]}'

# List all cached entries
python3 scripts/cache_manager.py list

# Show cache stats
python3 scripts/cache_manager.py stats

# Clear all cache
python3 scripts/cache_manager.py invalidate --all

# Clear specific query pattern
python3 scripts/cache_manager.py invalidate --pattern "timeout"
```

**Benefits:**
- Near-instant results for repeated queries
- SHA-256 cache keys (collision-resistant)
- Metadata tracking for debugging

### 4. `orchestrate.py`

Main orchestrator combining all optimizations.

```bash
# Basic usage
python3 scripts/orchestrate.py --query "find all errors"

# With all optimizations enabled (default)
python3 scripts/orchestrate.py \
    --query "find timeout errors" \
    --top-k 10 \
    --confidence 0.95 \
    --output results.json

# Disable optimizations
python3 scripts/orchestrate.py --query "find errors" --no-cache --no-early-exit

# Custom paths
python3 scripts/orchestrate.py \
    --query "analyze logs" \
    --state /path/to/state.pkl \
    --chunks-dir /path/to/chunks \
    --cache-dir /path/to/cache
```

**Features:**
- Query-guided chunk selection (`--top-k`)
- Result caching (enabled by default)
- Early exit on high confidence (`--confidence`)
- Progress tracking with confidence scores

## Quick Start Workflow

```bash
# 1. Initialize RLM state
python3 .claude/skills/rlm/scripts/rlm_repl.py init /path/to/large_file.txt

# 2. Create semantic chunks
python3 scripts/semantic_chunk.py --type auto

# 3. Process with orchestrator (all optimizations)
python3 scripts/orchestrate.py \
    --query "find all ERROR and WARNING messages" \
    --top-k 20 \
    --confidence 0.90

# 4. Results are cached for subsequent queries
python3 scripts/orchestrate.py \
    --query "count all ERROR messages" \
    --top-k 10  # Uses cached results from step 3
```

## Integration with Main Workflow

These scripts are called automatically when using the skill, but can also be used standalone:

```python
# In your own scripts
from scripts.rank_chunks import rank_chunks_by_query
from scripts.cache_manager import get_cached_result, cache_result

# Use functions directly
ranked = rank_chunks_by_query(content, "my query", top_k=5)
cached = get_cached_result(".cache", "chunk.txt", "my query")
```

## Performance Tips

1. **Always use query-guided selection** for targeted queries (5-10x faster)
2. **Use semantic chunking** when content structure is clear
3. **Enable caching** for iterative analysis on same file
4. **Set appropriate confidence threshold** (0.90-0.95 for most cases)
5. **Start with small `--top-k`** and increase if needed
