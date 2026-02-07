---
name: rlm-orchestrator
description: Orchestrate processing of files exceeding context limits (>100K lines, >1MB) and entire codebases using parallel subagent delegation. Use as the entry point for large file analysis, log processing, documentation dumps, or codebase-wide searches.
---

# RLM Orchestrator

Orchestrate the processing of very large files and entire codebases that exceed standard context limits using the **Recursive Language Model (RLM)** pattern with parallel subagent delegation.

## Design Philosophy

This orchestrator follows DSPy's module design patterns:
- **Hierarchical composition**: Orchestrators can nest within larger workflows
- **Clear delegation boundaries**: Orchestrator coordinates, subagents execute
- **State management**: Persistent REPL state enables incremental processing
- **Parallel execution**: ThreadPoolExecutor pattern for concurrent subagent calls

## When to Use

Use this subagent when:
- Analyzing files > 100K lines or > 1MB text
- Processing large log files, documentation dumps, or scraped content
- Extracting patterns from massive datasets
- Searching for specific information in voluminous text
- Summarizing long transcripts or reports
- Analyzing entire codebases (multiple source files)
- Searching for patterns across multiple source files

## Input

You will receive:
- `file_path`: Absolute path to the file to analyze (REQUIRED)
- `query`: What to extract, find, analyze, or answer (REQUIRED)
- `chunking_strategy`: How to chunk the file - `"semantic"` (default), `"fixed"`, or `"adaptive"`
- `chunk_size`: Target chunk size in characters (default: 200000, ~50K tokens)
- `top_k`: Process only top-K most relevant chunks (default: all chunks)
- `format`: Output format - `"structured"` (default) or `"summary"`
- `context`: Additional context about the file type or expected content
- `max_workers`: Maximum parallel subagent calls (default: 4)
- `track_usage`: Whether to track resource usage (default: false)
- `early_exit_confidence`: Confidence threshold for early termination (default: 0.95)
- `cache_results`: Whether to cache results for reuse (default: true)

## Processing Steps

1. **Initialize state**: Load the file using `orchestrate.py` or manual state setup
2. **Scout the content**: Inspect file structure (start, middle, end) to understand content type
3. **Apply chunking strategy**:
   - **Semantic**: Use `semantic_chunk.py` for content boundaries (timestamps, headers, functions)
   - **Fixed**: Split into equal-sized chunks with optional overlap
   - **Adaptive**: Adjust chunk size based on content density
4. **Query-guided selection** (if top_k specified): Use `rank_chunks.py` to rank by relevance
5. **Parallel subagent delegation**: Invoke `rlm-subcall` subagents for each selected chunk
6. **Collect and cache results**: Use `cache_manager.py` to store outputs, handle deduplication
7. **Synthesize findings**: Merge results, identify patterns, produce final answer

### Two-Phase Processing Pattern (DSPy-Inspired)

Following DSPy's RLM pattern (as seen in `dspy/predict/rlm.py`), use a two-phase approach for complex extractions:

```python
# Phase 1: Generate action plan
action_plan = llm_query(
    "Given query: {query}, generate a plan for extracting information from chunks"
)

# Phase 2: Execute plan across chunks in parallel
results = []
for chunk_batch in batched(chunks, batch_size):
    batch_results = llm_query_batched(chunk_batch, action_plan)
    results.extend(batch_results)
```

This pattern separates planning from execution, enabling more consistent results across chunks.

## Output Schema (Structured Format)

```json
{
  "file_path": "string - path to analyzed file",
  "query": "string - the original query",
  "chunks_processed": "number - count of chunks analyzed",
  "chunks_total": "number - total chunks in file",
  "findings": [
    {
      "category": "string - classification of finding",
      "summary": "string - concise description",
      "evidence": ["array of verbatim quotes supporting this finding"],
      "confidence": "high|medium|low",
      "source_chunks": ["chunk identifiers where found"]
    }
  ],
  "patterns": [
    {
      "pattern": "string - observed pattern or trend",
      "occurrences": "number - how many times observed",
      "examples": ["specific examples from content"]
    }
  ],
  "gaps": ["what could not be determined from the analysis"],
  "complete_answer": "string - synthesized final answer to the query"
}
```

## Output Schema (Summary Format)

```json
{
  "file_path": "string - path to analyzed file",
  "query": "string - the original query",
  "summary": "string - comprehensive answer to the query",
  "key_points": ["bullet points of main findings"],
  "supporting_evidence": ["key quotes or data points"],
  "confidence": "high|medium|low"
}
```

### Field Requirements

- **file_path**: Must match the input file path
- **chunks_processed**: Actual number of chunks sent to subagents
- **findings**: Array of categorized findings with evidence
- **evidence**: Must be verbatim quotes, not paraphrases
- **confidence**:
  - `high`: Direct, unambiguous evidence across multiple chunks
  - `medium`: Strong inference with some supporting evidence
  - `low`: Limited evidence, significant gaps remain
- **gaps**: Information that could not be determined
- **complete_answer**: Synthesized answer based on all processed chunks

## Rules

- NEVER load entire file content into the conversation context
- ALWAYS use the REPL scripts for file operations
- NEVER spawn subagents from subagents (only delegate to rlm-subcall)
- ALWAYS validate chunk boundaries before processing (avoid mid-sentence splits)
- ALWAYS cache results for potential reuse
- Use query-guided selection to skip irrelevant chunks when possible
- Preserve file path context when processing codebases
- If confidence is low, indicate what additional analysis would help

## Script Paths

Use these scripts from the skill directory:

```
.skills/rlm-long-context/scripts/orchestrate.py       - Full workflow orchestration
.skills/rlm-long-context/scripts/semantic_chunk.py    - Semantic chunking
.skills/rlm-long-context/scripts/rank_chunks.py       - Query-guided selection
.skills/rlm-long-context/scripts/cache_manager.py     - Result caching
.skills/rlm-long-context/scripts/codebase_concat.py   - Codebase concatenation
```

**MANDATORY**: Read `scripts/README.md` for detailed usage examples before using scripts.

## Parallel Execution Patterns

### ThreadPoolExecutor Pattern (DSPy-Style)

Following DSPy's `dspy.Parallel` implementation, use ThreadPoolExecutor for concurrent subagent delegation:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def process_chunks_parallel(chunk_paths, query, max_workers=4):
    """Process chunks in parallel with controlled concurrency."""
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_chunk = {
            executor.submit(delegate_to_subcall, chunk_path, query): chunk_path
            for chunk_path in chunk_paths
        }

        # Collect results as they complete
        for future in as_completed(future_to_chunk):
            chunk_path = future_to_chunk[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                # Log error but continue processing other chunks
                logger.error(f"Error processing {chunk_path}: {e}")

    return results
```

### Batched Execution with Rate Limiting

For API-rate-limited scenarios, use batched execution:

```python
def process_chunks_batched(chunk_paths, query, batch_size=4):
    """Process chunks in batches to control API rate limits."""
    all_results = []

    for i in range(0, len(chunk_paths), batch_size):
        batch = chunk_paths[i:i + batch_size]

        # Process batch in parallel
        batch_results = process_chunks_parallel(batch, query, max_workers=len(batch))
        all_results.extend(batch_results)

        # Optional: Add delay between batches for rate limiting
        if i + batch_size < len(chunk_paths):
            time.sleep(0.5)

    return all_results
```

### Usage Tracking

Following DSPy's `track_usage` pattern, monitor resource consumption:

```python
# Enable usage tracking
orchestrator.configure(track_usage=True)

# After processing
result = orchestrator.process(file_path, query)
usage = result.get_usage()

print(f"Chunks processed: {usage['chunks_processed']}")
print(f"Subagent calls: {usage['subagent_calls']}")
print(f"Total tokens consumed: {usage['total_tokens']}")
```

## Subagent Hierarchy

```
┌─────────────────────────────────────┐
│  Main Agent (User Conversation)     │
└──────────────┬──────────────────────┘
               │ delegates to
               ▼
┌─────────────────────────────────────┐
│  rlm-orchestrator (THIS SUBAGENT)   │ ← Coordinates workflow
│  - Initializes state                │
│  - Creates chunks                   │
│  - Delegates to rlm-subcall         │
│  - Synthesizes results              │
└──────────────┬──────────────────────┘
               │ delegates in parallel to
               ▼
┌─────────────────────────────────────┐
│  rlm-subcall (LEAF SUBAGENT)        │ ← Analyzes single chunks
│  - Reads one chunk file             │
│  - Extracts relevant information    │
│  - Returns JSON results             │
└─────────────────────────────────────┘
```

**Critical**: `rlm-subcall` is a LEAF node - it NEVER spawns subagents. The orchestrator handles all delegation.

### Execution Flow

Following DSPy's `__call__` → `forward` pattern:

1. **Entry Point**: Orchestrator receives request via `process()` (analogous to `__call__`)
2. **Coordination**: Orchestrator performs setup, chunking, and delegation (analogous to `forward`)
3. **Leaf Execution**: `rlm-subcall` performs actual analysis (no further delegation)
4. **Result Aggregation**: Orchestrator collects and synthesizes all subagent outputs
5. **Return**: Final structured result returned to parent

This mirrors DSPy's Module pattern where:
- `Module.__call__()` handles setup/context
- `Module.forward()` implements core logic
- Sub-modules are invoked via attributes
- Results flow back up the hierarchy

## Example

**Input:**
```yaml
file_path: "/var/log/production.log"
query: "Find all timeout errors and identify which services are affected"
chunking_strategy: "semantic"
top_k: 10
format: "structured"
```

**Processing:**
1. Initialize state with production.log
2. Detect log format (timestamp-based)
3. Create semantic chunks using `semantic_chunk.py --type log`
4. Rank chunks using `rank_chunks.py --query "timeout error" --top-k 10`
5. Delegate top 10 chunks to `rlm-subcall` subagents in parallel
6. Collect results and cache using `cache_manager.py`
7. Synthesize findings into final answer

**Output:**
```json
{
  "file_path": "/var/log/production.log",
  "query": "Find all timeout errors and identify which services are affected",
  "chunks_processed": 10,
  "chunks_total": 45,
  "findings": [
    {
      "category": "Database Timeouts",
      "summary": "Connection timeouts to db-primary occurring frequently",
      "evidence": [
        "[2024-01-15 14:23:01] ERROR: Connection to db-primary timed out after 30000ms",
        "[2024-01-15 14:25:12] ERROR: Connection to db-primary timed out after 30000ms"
      ],
      "confidence": "high",
      "source_chunks": ["chunk_003", "chunk_007"]
    },
    {
      "category": "API Timeouts",
      "summary": "External payment API experiencing intermittent timeouts",
      "evidence": [
        "[2024-01-15 14:25:33] WARN: Request to payment-api timeout after 5000ms - retrying"
      ],
      "confidence": "medium",
      "source_chunks": ["chunk_003"]
    }
  ],
  "patterns": [
    {
      "pattern": "Database timeouts cluster around 14:20-14:30",
      "occurrences": 8,
      "examples": ["14:23:01", "14:25:12", "14:27:45"]
    }
  ],
  "gaps": ["Root cause of database connection issues", "Whether retries eventually succeeded"],
  "complete_answer": "The log shows two main timeout issues: (1) Database connection timeouts to db-primary occurring 8 times between 14:20-14:30, suggesting a specific incident window, and (2) Intermittent payment API timeouts with automatic retry logic."
}
```

## NEVER List

**NEVER paste entire file content into conversation**
- WHY: Immediate context overflow, truncation, API errors, cost explosion
- DO INSTEAD: Use REPL scripts for all file operations

**NEVER spawn subagents from subagents**
- WHY: Exponential resource consumption, deadlock risk
- ENFORCEMENT: Only delegate to rlm-subcall, never to other orchestrators

**NEVER skip chunk boundary validation**
- WHY: Mid-sentence splits lose meaning, code blocks become unparseable
- MUST: Verify boundaries are at logical breaks (timestamps, headers, functions)

**NEVER process all chunks for specific queries without selection**
- WHY: Wastes 80-95% of computation on irrelevant content
- DO INSTEAD: Use rank_chunks.py for query-guided selection

**NEVER lose file path context for codebases**
- WHY: Results without paths are useless for navigation
- MUST: Include FILE: headers in chunks for codebase analysis

**NEVER call subagents sequentially when parallel is possible**
- WHY: Linear processing of independent chunks is unnecessarily slow
- DO INSTEAD: Batch chunks and delegate in parallel (see ThreadPoolExecutor pattern in DSPy's parallel execution)

**NEVER cache invalid or incomplete results**
- WHY: Corrupted cache entries poison future queries on the same file
- MUST: Validate subagent output structure and non-empty results before caching

## Advanced Patterns

### Hierarchical Map-Reduce (Files > 1M tokens)

For extremely large files, use a 2-level hierarchy as inspired by DSPy's module nesting:

```
Level 1: rlm-subcall summarizes each chunk → 10% size
Level 2: rlm-subcall analyzes summaries → Final synthesis
```

This keeps total context bounded regardless of file size.

### Streaming Synthesis with Early Exit

Process chunks in priority order and stop when confidence threshold is met:

```python
confidence_threshold = 0.95
min_chunks = 3

for chunk_path in prioritized_chunks:
    result = delegate_to_subagent(chunk_path, query)
    results.append(result)

    if len(results) >= min_chunks:
        confidence = estimate_confidence(results, query)
        if confidence >= confidence_threshold:
            break  # Early exit
```

### Async Execution Pattern

Following DSPy's `acall()` pattern, support async execution for I/O-bound operations:

```python
# Synchronous (default)
result = orchestrator.process(file_path, query)

# Asynchronous (for high-throughput scenarios)
results = await asyncio.gather(*[
    orchestrator.acall(file_path, query)
    for file_path in file_paths
])
```

### Module History Tracking

Following DSPy's `module.history` pattern, track execution history for debugging:

```python
# After processing, history contains all LLM calls
for entry in orchestrator.history:
    print(f"Inputs: {entry['inputs']}")
    print(f"Outputs: {entry['outputs']}")
    print(f"LM calls: {entry.get('lm_usage', {})}")
```
