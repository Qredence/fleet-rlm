---
name: rlm
description: Process very large context files (logs, docs, transcripts, scraped webpages) that exceed context limits by chunking content, delegating analysis to subagents, and synthesizing results. Use when files are >100k characters and require iterative inspection and extraction across the entire document.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - Task
---

# RLM (Recursive Language Model Workflow)

A workflow for processing large context files that won't fit in a single conversation by chunking content, delegating chunk analysis to subagents, and synthesizing results.

## When to Use This Skill

Use RLM when:
- The user provides or references a **very large context file** (>100k chars) - logs, documentation, transcripts, scraped webpages
- The file is too large to load entirely into the conversation context
- You need to **iteratively inspect, search, chunk, and extract** information from the entire file
- The task requires **analyzing multiple sections** independently before synthesis
- You need to maintain state across multiple operations on the same file

**Trigger phrases:**
- "Analyze this large log file..."
- "Extract all mentions of X from this transcript..."
- "Summarize this 500-page documentation..."
- "Find patterns across this entire scraped website dump..."

## Mental Model

The RLM workflow has three key components:

1. **Root LM** (you) - Orchestrates the workflow, manages chunking strategy, synthesizes final results
2. **Persistent REPL** (`rlm_repl.py`) - Maintains state, provides helpers for chunking/searching/extraction
3. **Sub-LM** (subagent) - Analyzes individual chunks and returns structured results

## Quick Start

### Minimal Example

```bash
# 1. Initialize with your large file
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py init /path/to/large_file.txt

# 2. Scout the content
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec -c "print(f'Total chars: {len(content):,}')"
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec -c "print(peek(0, 1000))"

# 3. Create chunks
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
paths = write_chunks('.letta/rlm_chunks', size=150000, overlap=5000)
print(f"Created {len(paths)} chunks")
for i, p in enumerate(paths[:3]):
    print(f"  {i}: {p}")
PY

# 4. Process chunks with subagent (see Subagent Approaches section)

# 5. Synthesize results in main conversation
```

## Complete Workflow

### 1. Parse Arguments

This skill expects `$ARGUMENTS` in one of these formats:
- `context=/path/to/file.txt query="What are the main errors?"`
- `context=/path/to/file.txt query="Extract all usernames" chunk_chars=200000`

Optional parameters:
- `chunk_chars=<int>` - Chunk size in characters (default: ~200000)
- `overlap_chars=<int>` - Overlap between chunks (default: 0)

If the user didn't provide arguments, ask for:
1. The context file path
2. The query/task

### 2. Initialize REPL State

```bash
# Initialize with the context file
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py init /path/to/context.txt

# Check status
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py status
```

The REPL creates a persistent state file at `.letta/rlm_state/state.pkl` containing:
- The full context content
- Buffers for accumulating results
- Any persisted variables from exec commands

### 3. Scout the Context

Get a quick sense of the content structure:

```bash
# Peek at start
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec -c "print(peek(0, 3000))"

# Peek at end
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec -c "print(peek(len(content)-3000, len(content)))"

# Quick search for structure
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec -c "hits = grep(r'^#+ ', max_matches=10); print([h['match'] for h in hits])"
```

### 4. Choose Chunking Strategy

**Semantic chunking** (preferred when structure is clear):
- Markdown files: Split on heading boundaries (`^#+ `)
- JSON: Split on top-level objects/arrays
- Logs: Split on timestamp boundaries
- Code: Split on function/class boundaries

**Character-based chunking** (fallback):
- Use when no clear semantic boundaries exist
- Recommended size: 150,000-250,000 chars
- Add overlap (5,000-10,000 chars) to preserve context at boundaries

### 5. Materialize Chunks

Write chunks to disk so subagents can read them:

```bash
# Character-based chunking
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
paths = write_chunks('.letta/rlm_chunks', size=200000, overlap=10000)
print(f"Created {len(paths)} chunks:")
for i, p in enumerate(paths):
    print(f"  chunk_{i}: {p}")
PY

# Semantic chunking example (markdown headings)
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
import re
# Find all heading positions
headings = list(re.finditer(r'^#+ .+$', content, re.MULTILINE))
# Create chunks between headings
# ... (custom logic based on content structure)
PY
```

### 6. Process Chunks with Subagent

You have two approaches for delegating chunk analysis:

#### Approach A: Letta Task Tool (Recommended)

```python
# For each chunk, spawn a subagent task
Task({
    "subagent_type": "general-purpose",
    "description": f"Analyze chunk {i}",
    "prompt": f"""Analyze this chunk and extract all error messages.

Read the chunk: {chunk_path}

Return a JSON object with:
- errors: list of error messages found
- severity: list of severity levels
- context: brief context for each error

Be concise and structured."""
})
```

#### Approach B: Custom rlm-subcall Subagent

If you've set up a dedicated `rlm-subcall` agent (see `references/subagent-setup.md`):

```bash
# Use your preferred method to invoke rlm-subcall with:
# - The user's query
# - The chunk file path
# - Specific extraction instructions
```

**Key principles for subagent calls:**
- Keep instructions specific and task-focused
- Request structured output (JSON preferred)
- Limit subagent output size (they should extract/summarize, not dump)
- Pass chunk file path, don't paste content into prompt

### 7. Accumulate Results

Collect subagent outputs in REPL buffers or in the main conversation:

```bash
# Add subagent result to buffers
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
result = """
{chunk analysis from subagent}
"""
add_buffer(result)
print(f"Buffer count: {len(buffers)}")
PY
```

Or manually track results in the conversation as you iterate through chunks.

### 8. Synthesize Final Answer

Once all chunks are processed:

1. Review accumulated results from buffers/conversation
2. Identify patterns, trends, or comprehensive answers
3. Synthesize a coherent final response
4. Optionally: Use a subagent one final time to merge/format the collected evidence

```bash
# Export buffers for final synthesis
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py export-buffers /tmp/rlm_results.txt
```

## REPL Helper Functions

The `rlm_repl.py` script provides these helpers in exec mode:

### Variables
- `context` - Dict with keys: `path`, `loaded_at`, `content`
- `content` - String alias for `context['content']`
- `buffers` - List[str] for storing intermediate results

### Functions
- `peek(start=0, end=1000)` - Extract a substring from content
- `grep(pattern, max_matches=20, window=120, flags=0)` - Search with regex, returns matches with context
- `chunk_indices(size=200000, overlap=0)` - Calculate chunk boundaries (returns list of (start, end) tuples)
- `write_chunks(out_dir, size=200000, overlap=0, prefix='chunk')` - Write chunks to files, returns list of paths
- `add_buffer(text)` - Append text to buffers list

### Commands
- `init <context_path>` - Load a context file and create state
- `status` - Show current state (file size, buffers, vars)
- `exec -c "<code>"` - Execute Python code (state persists)
- `exec` - Execute Python code from stdin (use with heredoc)
- `export-buffers <out_path>` - Write all buffers to a file
- `reset` - Delete state file

## Guardrails

**DO:**
- Use REPL to locate and extract specific excerpts
- Keep subagent prompts focused and structured
- Request JSON or other structured formats from subagents
- Quote only necessary snippets in the main conversation

**DON'T:**
- Paste large raw chunks into the main chat context
- Spawn subagents from within subagents (orchestration stays in root)
- Store sensitive data in REPL state files
- Forget to clean up `.letta/rlm_state/` and `.letta/rlm_chunks/` when done

## File Locations

By default, RLM uses these paths:
- State: `.letta/rlm_state/state.pkl`
- Chunks: `.letta/rlm_chunks/chunk_*.txt`
- Buffers export: User-specified path

You can override the state path with `--state /custom/path.pkl` on any command.

## Advanced Usage

See `references/examples.md` for complete workflow examples including:
- Analyzing large log files
- Extracting structured data from transcripts
- Summarizing documentation
- Finding patterns in scraped web content

For subagent setup details, see `references/subagent-setup.md`.

## Troubleshooting

**"No state found" error:**
- Run `init` first with your context file path

**REPL state getting too large:**
- Use `reset` to clear state
- Initialize with a fresh context
- Consider filtering content before init if possible

**Subagent outputs too verbose:**
- Be more specific in subagent prompts
- Request structured/summarized output
- Limit output with explicit constraints

**Memory issues with very large files:**
- Use `--max-bytes` with init to cap file size
- Pre-filter or split files before processing
- Process in multiple RLM sessions if needed
