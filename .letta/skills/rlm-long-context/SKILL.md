---
name: rlm-long-context
description: Process files exceeding context limits (>100K lines, >1MB) and entire codebases using parallel subagent delegation and persistent REPL. Use when (1) analyzing large log files, (2) searching massive text dumps, (3) extracting patterns from voluminous data, (4) summarizing long transcripts, (5) processing documentation dumps, (6) analyzing entire codebases, (7) searching across multiple source files. 
metadata: large files, log analysis, chunk processing, map-reduce, context overflow, 100K lines, big data, codebase analysis, repository search, multi-file processing
---

# RLM Long-Context Processing

Process very large files (logs, docs, transcripts) that exceed standard context limits using a **Recursive Language Model (RLM)** pattern with parallel subagent delegation.

## When to Use This Skill

Use this skill when:
- Analyzing files > 100K lines or > 1MB text
- Processing large log files, documentation dumps, or scraped content
- Extracting patterns from massive datasets
- Searching for specific information in voluminous text
- Summarizing long transcripts or reports
- **Analyzing entire codebases** (see "Processing a Whole Codebase" section below)
- Searching for patterns across multiple source files
- Finding function definitions, imports, or dependencies across a project

## Scripts Reference

This skill includes helper scripts in `scripts/`:

| Script | Purpose |
|--------|---------|
| `rank_chunks.py` | Query-guided chunk selection (5-10x speedup) |
| `semantic_chunk.py` | Content-aware chunking by boundaries |
| `cache_manager.py` | Result caching for repeated queries |
| `orchestrate.py` | Main orchestrator with all optimizations |

**MANDATORY - READ ENTIRE FILE**: Before using scripts, read [`scripts/README.md`](scripts/README.md) for detailed usage and examples.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Main Agent (Orchestrator)                        │
│  ├─ Persistent Python REPL with loaded content                      │
│  ├─ Query-Guided Selection (filter chunks by relevance)             │
│  ├─ Semantic Chunking (content-aware boundaries)                    │
│  ├─ Adaptive Sizing (density-based chunk sizes)                     │
│  └─ Parallel subagent delegation with caching                       │
└─────────────────────────────────────────────────────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │  Relevance Score │  │  Relevance Score │  │  Relevance Score │
    │      0.95        │  │      0.72        │  │      0.15        │
    │  [Process First] │  │  [Process Next]  │  │  [Skip / Low]    │
    └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
             │                     │                     │
             ▼                     ▼                     ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │    Chunk A       │  │    Chunk B       │  │    Chunk C       │
    │  (semantic)      │  │  (semantic)      │  │  (semantic)      │
    └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
             │                     │                     │
             ▼                     ▼                     ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │   rlm-subcall    │  │   rlm-subcall    │  │   (skipped)      │
    │   (subagent)     │  │   (subagent)     │  │                  │
    └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   │
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
    ┌──────────────────────┐                ┌──────────────────────┐
    │   Result Caching     │                │  Streaming Results   │
    │   (deduplication)    │                │  (early exit if      │
    └──────────┬───────────┘                │   confidence high)   │
               │                            └──────────┬───────────┘
               │                                       │
               └───────────────────┬───────────────────┘
                                   ▼
                    ┌────────────────────────┐
                    │  Hierarchical Merge    │
                    │  (if >1M tokens:       │
                    │   chunk → summary →    │
                    │   final synthesis)     │
                    └───────────┬────────────┘
                                ▼
                    ┌────────────────────────┐
                    │     Final Answer       │
                    │    (Main Agent)        │
                    └────────────────────────┘
```

## Workflow

### 1. Initialize REPL State

Load the large context file into a persistent Python REPL:

```bash
# Initialize with context file
python3 .skills/rlm-long-context/scripts/rlm_repl.py init <context_path>

# Verify loaded
python3 .skills/rlm-long-context/scripts/rlm_repl.py status
```

### 2. Scout the Content

Quickly inspect structure before chunking:

```bash
# View start, middle, and end to understand structure
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec -c "print(peek(0, 3000))"
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec -c "print(peek(len(content)//2, len(content)//2 + 3000))"
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec -c "print(peek(len(content)-3000, len(content)))"
```

### 3. Query-Guided Chunk Selection (NEW)

**Before processing all chunks, filter by relevance.**

**MANDATORY - READ ENTIRE FILE**: For targeted queries, you MUST read [`scripts/rank_chunks.py`](scripts/rank_chunks.py) completely to understand the ranking implementation.

**Do NOT load** `scripts/semantic_chunk.py` for this step — ranking uses its own chunking logic.

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

### 4. Semantic Chunking (NEW)

Use content boundaries instead of fixed sizes.

**MANDATORY - READ ENTIRE FILE**: For structured content (logs, markdown, code), you MUST read [`scripts/semantic_chunk.py`](scripts/semantic_chunk.py) completely to understand boundary detection.

**Do NOT load** `scripts/rank_chunks.py` when using semantic chunking — they are alternative approaches.

```bash
# Using semantic_chunk.py script (auto-detects content type)
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --state .claude/rlm_state/state.pkl

# Force specific content type
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --type log
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --type markdown
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --type json
python3 .skills/rlm-long-context/scripts/semantic_chunk.py --type python
```

| Content Type | Boundary Pattern |
|--------------|------------------|
| **Markdown** | Headers (`^#+ `) |
| **Logs** | Timestamps (`^\d{4}-\d{2}-\d{2}`) |
| **JSON** | Top-level objects/arrays |
| **Code** | Function/class definitions |

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

**Benefit:** Preserves context boundaries, reduces "split mid-sentence" issues.

### 5. Adaptive Chunk Sizing (NEW)

Start small, expand if needed based on content density.

**When to use**: Mixed-density content where some sections need detail and others don't.

```bash
# Start with small chunks, merge if uncertain
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
def adaptive_chunks(content, initial_size=50000, max_size=200000):
    """Create chunks that adapt based on content density."""
    chunks = []
    i = 0
    while i < len(content):
        # Start with initial size
        end = min(i + initial_size, len(content))
        chunk = content[i:end]
        
        # Check information density (e.g., keywords per char)
        density = len(re.findall(r'error|exception|fail', chunk, re.I)) / len(chunk)
        
        # Expand if density is low (need more context)
        while density < 0.001 and end - i < max_size and end < len(content):
            end = min(end + initial_size, len(content))
            chunk = content[i:end]
            density = len(re.findall(r'error|exception|fail', chunk, re.I)) / len(chunk)
        
        chunks.append((i, end, chunk))
        i = end
    
    return chunks

# Generate adaptive chunks
chunks = adaptive_chunks(content)
print(f"Created {len(chunks)} adaptive chunks")
PY
```

**Benefit:** Dense content gets smaller chunks (more detail), sparse content gets larger chunks (efficiency).

### 6. Hierarchical Map-Reduce (NEW)

For files > 1M tokens, use 2-level processing to keep context bounded.

```
Level 1: Subagents summarize each chunk → 10% size
Level 2: Subagents analyze summaries → Final synthesis
```

```bash
# Phase 1: Summarize chunks
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
# Delegate to subagents with "summarize" instruction
summary_tasks = []
for chunk_path in chunk_paths:
    task = {
        "subagent": "rlm-subcall",
        "instruction": "summarize",
        "chunk_path": chunk_path,
        "max_output": 500  # Force concise summary
    }
    summary_tasks.append(task)

print(f"Phase 1: {len(summary_tasks)} summary tasks")
PY

# Phase 2: Analyze summaries
# ... subagents analyze the condensed summaries
# Phase 3: Final synthesis in main agent
```

**Benefit:** Keeps total context bounded regardless of file size.

### 7. Streaming Synthesis with Early Exit (NEW)

Process results incrementally, stop when confident.

**MANDATORY - READ ENTIRE FILE**: For iterative analysis, read [`scripts/orchestrate.py`](scripts/orchestrate.py) which implements streaming with early exit.

```bash
# Process chunks in priority order, stop early if possible
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
results = []
confidence_threshold = 0.95
min_chunks = 3

for chunk_path in prioritized_chunks:
    # Delegate to subagent
    result = delegate_to_subagent(chunk_path, query)
    results.append(result)
    
    # Check if we have enough information
    if len(results) >= min_chunks:
        confidence = estimate_confidence(results, query)
        if confidence >= confidence_threshold:
            print(f"Early exit after {len(results)} chunks (confidence: {confidence:.2f})")
            break

print(f"Processed {len(results)} of {len(prioritized_chunks)} chunks")
PY
```

**Benefit:** Skip remaining chunks if early results provide sufficient answer.

### 8. Parallel Subagent Delegation

For each selected chunk, invoke the `rlm-subcall` subagent (defined in `.agents/rlm-subcall.md`) with:
- The user query
- The chunk file path  
- Specific extraction instructions

**Subagent location:** `.agents/rlm-subcall.md`

**Key delegation pattern:**
```yaml
subagent: rlm-subcall
input:
  query: "Find all ERROR entries and their timestamps"
  chunk_path: ".claude/rlm_state/chunks/chunk_001.txt"
  chunk_id: "chunk_001"
  format: "json"
```

**Subagent output format:**
```json
{
  "chunk_id": "chunk_001",
  "relevant": [{"point": "...", "evidence": "...", "confidence": "high"}],
  "missing": ["what could not be determined"],
  "suggested_queries": ["follow-up questions"],
  "complete_answer": null
}
```

### 9. Result Caching (NEW)

Cache chunk analyses for repeated queries.

**MANDATORY - READ ENTIRE FILE**: For repeated queries on same file, read [`scripts/cache_manager.py`](scripts/cache_manager.py) for cache management.

**Do NOT implement your own caching** — use the provided cache manager to ensure consistency.

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
import hashlib
import json

def get_cached_result(chunk_path, query):
    """Check if we've already analyzed this chunk for this query."""
    cache_key = hashlib.md5(f"{chunk_path}:{query}".encode()).hexdigest()
    cache_file = f".claude/rlm_state/cache/{cache_key}.json"
    
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)
    return None

def cache_result(chunk_path, query, result):
    """Cache the analysis result."""
    cache_key = hashlib.md5(f"{chunk_path}:{query}".encode()).hexdigest()
    cache_file = f".claude/rlm_state/cache/{cache_key}.json"
    
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, 'w') as f:
        json.dump(result, f)

# Use caching in delegation loop
for chunk_path in chunk_paths:
    cached = get_cached_result(chunk_path, query)
    if cached:
        results.append(cached)
        continue
    
    # Delegate to subagent
    result = delegate_to_subagent(chunk_path, query)
    cache_result(chunk_path, query, result)
    results.append(result)
PY
```

**Benefit:** Avoid re-analyzing chunks for repeated queries on the same file.

### 10. Collect Results

Subagents return structured outputs (JSON preferred). Append to buffers:

```python
# In REPL
add_buffer("errors", {"chunk": 1, "findings": [...]})
add_buffer("errors", {"chunk": 2, "findings": [...]})
```

### 11. Synthesize

Once sufficient evidence is collected:
- Merge buffers in main agent
- Identify patterns across chunks
- Produce final answer

Optional: One final subagent call to merge buffers into a coherent draft.

## NEVER List

**NEVER paste entire chunks into main chat context**
- WHY: Immediate context overflow (>200K tokens), truncation of critical sections, API errors, exponential cost increase
- COST: Lost information, failed analysis, wasted tokens, incomplete results
- DO INSTEAD: Use REPL to locate excerpts; quote only findings (typically <1KB)

**NEVER spawn subagents from subagents**
- WHY: Exponential resource consumption, deadlock risk, worker pool exhaustion
- RESULT: Hanging tasks, system instability, unrecoverable state
- ENFORCEMENT: Orchestration stays strictly in main agent; subagents are leaf nodes only

**NEVER split content mid-logical-unit**
- WHY: Timestamps split across chunks lose causality; code blocks split become unparseable; sentences split lose meaning
- DETECTION: Check for incomplete lines at chunk edges (open brackets, partial timestamps)
- FIX: Use semantic chunking with boundary detection; verify boundaries before processing

**NEVER skip result validation before caching**
- WHY: Corrupted/incomplete results poison the cache for all future queries
- COST: Repeated queries return wrong answers; silent data corruption
- MUST: Verify JSON structure, non-empty results, and valid chunk_id before caching

**NEVER use fixed-size chunks without overlap for structured data**
- WHY: Headers/metadata at chunk edges get isolated from their content; JSON objects split become invalid
- FIX: Use 10% overlap minimum or semantic boundaries; test chunk validity

**NEVER process all chunks when query is specific**
- WHY: Wastes 80-95% of computation on irrelevant content
- FIX: Use query-guided selection first; process only top-K relevant chunks

## Command Reference

### REPL Commands

| Command | Description |
|---------|-------------|
| `init <path>` | Load context file into REPL |
| `status` | Show loaded file info |
| `exec -c "<code>"` | Execute Python code |
| `exec <<'PY'...PY` | Execute multi-line Python |

### REPL Helper Functions

| Function | Description |
|----------|-------------|
| `peek(start, end)` | Extract character range |
| `write_chunks(dir, size, overlap)` | Split and save chunks |
| `add_buffer(name, data)` | Accumulate subagent results |
| `get_buffer(name)` | Retrieve accumulated data |

## Example Usage

### Analyzing a Large Log File

```bash
# User request: "Find all timeout errors in production.log"

# 1. Initialize
python3 .skills/rlm-long-context/scripts/rlm_repl.py init /var/log/production.log

# 2. Scout
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec -c "print(peek(0, 1000))"

# 3. Chunk by log entries (semantic)
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
# Split by timestamp pattern
import re
entries = re.split(r'\n(?=\d{4}-\d{2}-\d{2})', content)
# Write in batches
paths = write_chunks('.claude/rlm_state/chunks', size=200000, overlap=0)
print(f"Created {len(paths)} chunks from {len(entries)} log entries")
PY

# 4-5. Delegate to subagents (parallel)
# For each chunk_XXX.txt → rlm-subcall → collect timeout errors

# 6. Synthesize findings
```

### Extracting API Endpoints from Documentation

```bash
# User request: "List all POST endpoints from api-docs.md"

# 1-3. Initialize, scout, chunk
python3 .skills/rlm-long-context/scripts/rlm_repl.py init docs/api-reference.md
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
paths = write_chunks('.claude/rlm_state/chunks', size=150000, overlap=500)
PY

# 4-5. Subagents extract POST endpoints with paths and descriptions
# 6. Merge into final endpoint catalog
```

## Optimization Summary

The enhanced RLM workflow includes these efficiency improvements:

| Technique | When to Use | Impact |
|-----------|-------------|--------|
| **Query-Guided Selection** | When query has clear keywords | 5-10x speedup |
| **Semantic Chunking** | Structured content (logs, markdown, JSON) | Better context preservation |
| **Adaptive Sizing** | Mixed-density content | Balanced detail/efficiency |
| **Hierarchical Map-Reduce** | Files > 1M tokens | Bounded context regardless of size |
| **Streaming + Early Exit** | When partial answers acceptable | Variable speedup |
| **Result Caching** | Repeated queries on same files | Near-instant for cached chunks |

## Processing a Whole Codebase

For analyzing entire codebases (multiple files), use the **concatenation approach**:

### Step 1: Concatenate Codebase

```bash
# Concatenate all source files into single processable file
python3 .skills/rlm-long-context/scripts/codebase_concat.py \
    /path/to/your/project \
    -o codebase.txt

# Include only specific file types
python3 .skills/rlm-long-context/scripts/codebase_concat.py \
    /path/to/your/project \
    -o codebase.txt \
    -i '*.py' '*.md' '*.yaml'

# Exclude specific directories
python3 .skills/rlm-long-context/scripts/codebase_concat.py \
    /path/to/your/project \
    -o codebase.txt \
    --exclude-dirs node_modules vendor .git
```

**Output Format:**
```
======== FILE: src/main.py ========
<content of main.py>
======== END FILE: src/main.py ========

======== FILE: src/utils.py ========
<content of utils.py>
======== END FILE: src/utils.py ========
```

### Step 2: Process with RLM

```bash
# Initialize with concatenated codebase
python3 .skills/rlm-long-context/scripts/rlm_repl.py init codebase.txt

# Scout the structure
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec -c "
import re
files = re.findall(r'FILE: (.+)', content)
print(f'Total files: {len(files)}')
print(f'First 10 files: {files[:10]}')
"
```

### Step 3: Semantic Chunking for Code

**MANDATORY - READ ENTIRE FILE**: For codebases, you MUST read [`scripts/semantic_chunk.py`](scripts/semantic_chunk.py) with `--type python` (or appropriate language).

```bash
# Auto-detect code type and chunk by function/class boundaries
python3 .skills/rlm-long-context/scripts/semantic_chunk.py \
    --type python \
    --state .claude/rlm_state/state.pkl
```

Or manually in REPL:

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
import re
import os

# Split by file boundaries (created by codebase_concat.py)
file_pattern = r'={60}\nFILE: (.+?)\n={60}\n(.*?)\n={60}\nEND FILE'
files = re.findall(file_pattern, content, re.DOTALL)

print(f"Found {len(files)} files")

# Create chunks directory
chunks_dir = '.claude/rlm_state/chunks'
os.makedirs(chunks_dir, exist_ok=True)

# Write each file as a chunk (or split large files further)
chunk_paths = []
for i, (filepath, filecontent) in enumerate(files):
    chunk_path = f"{chunks_dir}/chunk_{i:04d}.txt"
    with open(chunk_path, 'w') as f:
        f.write(f"FILE: {filepath}\n\n{filecontent}")
    chunk_paths.append(chunk_path)

print(f"Created {len(chunk_paths)} chunks")
PY
```

### Step 4: Query-Guided File Selection

**For targeted queries**, find relevant files first:

```bash
python3 .skills/rlm-long-context/scripts/rlm_repl.py exec <<'PY'
import re

# Find files matching query keywords
keywords = ['auth', 'login', 'password']  # your search terms
pattern = re.compile('|'.join(keywords), re.IGNORECASE)

# Extract file list with content
file_pattern = r'={60}\nFILE: (.+?)\n={60}\n(.*?)\n={60}\nEND FILE'
files = re.findall(file_pattern, content, re.DOTALL)

# Score files by relevance
scored_files = []
for filepath, filecontent in files:
    score = len(pattern.findall(filecontent))
    if score > 0:
        scored_files.append((filepath, score, filecontent))

# Sort by relevance
top_files = sorted(scored_files, key=lambda x: x[1], reverse=True)[:10]

print(f"Top {len(top_files)} relevant files:")
for filepath, score, _ in top_files:
    print(f"  {filepath} (matches: {score})")
PY
```

### Step 5: Delegation with File Context

When delegating to subagents, include file path:

```yaml
subagent: rlm-subcall
input:
  query: "Find all authentication-related functions"
  chunk_path: ".claude/rlm_state/chunks/chunk_0005.txt"
  chunk_id: "chunk_0005"
  format: "json"
  context: "This chunk contains src/auth.py"
```

### Codebase-Specific Queries

| Query Type | Approach | Example |
|------------|----------|---------|
| **Find function definitions** | Query-guided: search `def function_name` | "Find all functions named 'authenticate'" |
| **Cross-file dependencies** | Process all chunks, aggregate imports | "What files import the User class?" |
| **Architecture overview** | Semantic chunking by file, summarize each | "Summarize the project structure" |
| **Security audit** | Query-guided: keywords like `password`, `secret`, `token` | "Find hardcoded secrets" |
| **Refactoring candidates** | Query: `TODO`, `FIXME`, duplicate code patterns | "Find duplicate utility functions" |

### Extracting Specific Files

After processing, extract individual files:

```bash
# Extract a specific file from concatenated output
python3 .skills/rlm-long-context/scripts/codebase_concat.py \
    codebase.txt \
    --extract src/auth.py \
    -o auth_backup.py
```

### NEVER List for Codebases

**NEVER concatenate node_modules, .git, or vendor directories**
- WHY: Massive bloat, irrelevant noise, potential security issues
- COST: 100MB+ files, impossible to process
- FIX: Use `--exclude-dirs` flag

**NEVER lose file path context**
- WHY: Search results without paths are useless for code navigation
- COST: Cannot locate and fix issues
- FIX: Always include `FILE: <path>` header in chunks

**NEVER process binary files as text**
- WHY: Binary content corrupts analysis, creates noise
- COST: False positives, garbled output
- FIX: Exclude: `*.png`, `*.jpg`, `*.exe`, `*.so`, `*.dll`

## Related Patterns

- **Map-Reduce**: Similar distributed processing pattern
- **Embedding Search**: Pre-index chunks for semantic similarity queries
- **Streaming Processing**: Process chunks as they're generated vs. batch

## Limitations

- Subagent outputs accumulate in main context — monitor total size
- Parallel execution limited by available subagent workers
- File must fit in REPL memory (typically 2-4GB)
- No automatic retry on subagent failure (implement manually)
