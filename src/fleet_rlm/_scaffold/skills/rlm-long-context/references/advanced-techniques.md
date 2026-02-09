# Advanced Long-Context Techniques

## Contents

- Query-Guided Chunk Selection
- Semantic Chunking
- Adaptive Chunk Sizing
- Hierarchical Map-Reduce
- Streaming Synthesis with Early Exit
- Result Caching

---

## Query-Guided Chunk Selection

**Before processing all chunks, filter by relevance.**

Read [`scripts/rank_chunks.py`](../scripts/rank_chunks.py) for the ranking implementation.

```bash
# Using the rank_chunks.py script
python3 .skills/rlm-long-context/scripts/rank_chunks.py \
    --query "timeout error" \
    --top-k 10 \
    -o relevant_chunks.txt

# Or manually in REPL
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
import re

# Define query-relevant keywords
keywords = ["timeout", "error", "exception", "failed"]
pattern = re.compile("|".join(keywords), re.IGNORECASE)

# Score each potential chunk
chunk_size = 200000
scores = []
for i in range(0, len(content), chunk_size):
    chunk = content[i:i+chunk_size]
    score = len(pattern.findall(chunk))
    scores.append((i, score))

# Sort by relevance score
top_chunks = sorted(scores, key=lambda x: x[1], reverse=True)[:5]
print(f"Top 5 relevant chunks: {[c[0] for c in top_chunks]}")
PY
```

**Benefit:** Process only the most relevant chunks first, skip irrelevant ones (5-10x speedup).

---

## Semantic Chunking

Use content boundaries instead of fixed sizes.

Read [`scripts/semantic_chunk.py`](../scripts/semantic_chunk.py) for boundary detection.

```bash
# Using semantic_chunk.py script (auto-detects content type)
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --state .claude/rlm_state/state.pkl

# Force specific content type
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --type log
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --type markdown
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --type json
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --type python
```

| Content Type | Boundary Pattern                  |
| ------------ | --------------------------------- |
| **Markdown** | Headers (`^#+ `)                  |
| **Logs**     | Timestamps (`^\d{4}-\d{2}-\d{2}`) |
| **JSON**     | Top-level objects/arrays          |
| **Code**     | Function/class definitions        |

**Manual approach in REPL:**

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
import re

# Split on timestamp boundaries (log files)
timestamps = list(re.finditer(r'\n\d{4}-\d{2}-\d{2}T', content))
boundaries = [0] + [m.start() for m in timestamps] + [len(content)]

# Create semantic chunks
chunks_dir = '.claude/rlm_state/chunks'
os.makedirs(chunks_dir, exist_ok=True)
paths = []

for i in range(len(boundaries)-1):
    start, end = boundaries[i], boundaries[i+1]
    chunk = content[start:end]
    path = f"{chunks_dir}/chunk_{i:04d}.txt"
    with open(path, 'w') as f:
        f.write(chunk)
    paths.append(path)

print(f"Created {len(paths)} semantic chunks")
PY
```

---

## Adaptive Chunk Sizing

Start small, expand if needed based on content density.

**When to use**: Mixed-density content where some sections need detail and others don't.

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
def adaptive_chunks(content, initial_size=50000, max_size=200000):
    """Create chunks that adapt based on content density."""
    chunks = []
    i = 0
    while i < len(content):
        end = min(i + initial_size, len(content))
        chunk = content[i:end]

        density = len(re.findall(r'error|exception|fail', chunk, re.I)) / len(chunk)

        while density < 0.001 and end - i < max_size and end < len(content):
            end = min(end + initial_size, len(content))
            chunk = content[i:end]
            density = len(re.findall(r'error|exception|fail', chunk, re.I)) / len(chunk)

        chunks.append((i, end, chunk))
        i = end

    return chunks

chunks = adaptive_chunks(content)
print(f"Created {len(chunks)} adaptive chunks")
PY
```

**Benefit:** Dense content gets smaller chunks (more detail), sparse content gets larger chunks (efficiency).

---

## Hierarchical Map-Reduce

For files > 1M tokens, use 2-level processing to keep context bounded.

```
Level 1: Subagents summarize each chunk → 10% size
Level 2: Subagents analyze summaries → Final synthesis
```

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
summary_tasks = []
for chunk_path in chunk_paths:
    task = {
        "subagent": "rlm-subcall",
        "instruction": "summarize",
        "chunk_path": chunk_path,
        "max_output": 500
    }
    summary_tasks.append(task)

print(f"Phase 1: {len(summary_tasks)} summary tasks")
PY
```

---

## Streaming Synthesis with Early Exit

Process results incrementally, stop when confident.

Read [`scripts/orchestrate.py`](../scripts/orchestrate.py) for streaming with early exit.

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
results = []
confidence_threshold = 0.95
min_chunks = 3

for chunk_path in prioritized_chunks:
    result = delegate_to_subagent(chunk_path, query)
    results.append(result)

    if len(results) >= min_chunks:
        confidence = estimate_confidence(results, query)
        if confidence >= confidence_threshold:
            print(f"Early exit after {len(results)} chunks (confidence: {confidence:.2f})")
            break

print(f"Processed {len(results)} of {len(prioritized_chunks)} chunks")
PY
```

---

## Result Caching

Cache chunk analyses for repeated queries.

Read [`scripts/cache_manager.py`](../scripts/cache_manager.py) for cache management.

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
import hashlib
import json

def get_cached_result(chunk_path, query):
    cache_key = hashlib.md5(f"{chunk_path}:{query}".encode()).hexdigest()
    cache_file = f".claude/rlm_state/cache/{cache_key}.json"
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)
    return None

def cache_result(chunk_path, query, result):
    cache_key = hashlib.md5(f"{chunk_path}:{query}".encode()).hexdigest()
    cache_file = f".claude/rlm_state/cache/{cache_key}.json"
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, 'w') as f:
        json.dump(result, f)

for chunk_path in chunk_paths:
    cached = get_cached_result(chunk_path, query)
    if cached:
        results.append(cached)
        continue
    result = delegate_to_subagent(chunk_path, query)
    cache_result(chunk_path, query, result)
    results.append(result)
PY
```
