---
name: rlm
description: Run the Recursive Language Model using fleet-rlm for long-context tasks. Uses a persistent Modal Sandbox Python REPL and an rlm-subcall subagent as the sub-LLM (llm_query). Use when processing large files, extracting information from documents, or running code in cloud sandboxes.
---

# RLM — Modal Sandbox Long-Context Skill

Process files exceeding context limits using DSPy's Recursive Language Model
backed by **Modal cloud sandboxes**. The sandbox is a persistent Python REPL
where code navigates data programmatically; the `rlm-subcall` subagent acts
as the sub-LLM for semantic analysis of individual chunks.

## Delegation Guidance

This skill provides **domain knowledge** — load it for RLM best practices. For
**execution delegation**, spawn the fleet-rlm agent team:

### Quick Agent Spawning

```python
# Process large documents
Task("rlm-orchestrator", "Process <file> and extract <information>")

# Debug RLM issues
Task("rlm-specialist", "Debug this RLM failure: <error>")

# Check Modal setup
Task("modal-interpreter-agent", "Verify Modal credentials and sandbox")

# Analyze a single chunk
Task("rlm-subcall", "Analyze chunk at <path> for <query>")
```

### Detailed Delegation Matrix

| Scenario                                | Approach                                                         |
| --------------------------------------- | ---------------------------------------------------------------- |
| Process a large file inline             | Load this skill, use ModalInterpreter directly                   |
| Delegate large-file processing          | Spawn `rlm-orchestrator` agent (auto-loads this skill)           |
| Analyze individual chunks               | Spawn `rlm-subcall` agent (leaf node)                            |
| Debug a failing pipeline                | Spawn `rlm-specialist` agent (auto-loads rlm-debug)              |
| Check Modal infrastructure              | Spawn `modal-interpreter-agent`                                  |
| Parallel document analysis (agent team) | Spawn multiple agents, they coordinate via shared task list      |

> **Synergy**: Skills inject knowledge; agents provide isolated execution contexts.
> Spawn `rlm-orchestrator` for complex tasks - it loads this skill + `rlm-execute` + `rlm-memory`.

## New Features (DSPy RLM Aligned)

### Built-in Sandbox Tools

Code running in the sandbox now has access to:

| Tool | Description | Example |
|------|-------------|---------|
| `llm_query(prompt)` | Single sub-LLM call | `result = llm_query("Summarize: " + text)` |
| `llm_query_batched(prompts)` | Parallel sub-LLM calls | `results = llm_query_batched(["Q1", "Q2", "Q3"])` |
| `SUBMIT(**kwargs)` | Return structured output | `SUBMIT(answer=42, status="ok")` |
| `Final = value` | Alternative to SUBMIT (RLM paper) | `Final = {"answer": 42}` |

### Configuration Options

```python
ModalInterpreter(
    timeout=600,                    # Sandbox lifetime
    volume_name='rlm-volume-dspy',  # V2 volume for persistence
    max_llm_calls=50,               # Limit sub-LLM calls (default: 50)
    sub_lm=cheap_lm,                # Use different LM for sub-queries
    summarize_stdout=True,          # Metadata-only for long outputs
    stdout_summary_threshold=500,   # Threshold for summarization
)
```

### Output Conventions

Two ways to return results from sandbox:

```python
# Option 1: SUBMIT (traditional)
SUBMIT(answer="result", confidence="high")

# Option 2: Final variable (RLM paper convention)
Final = {"answer": "result", "confidence": "high"}
```

Both are equivalent - use whichever is more natural for your code.

## Additional Resources

- For complete ModalInterpreter API, sandbox helpers, DSPy signatures, and troubleshooting, see [references/api-reference.md](references/api-reference.md)

## Prerequisites

1. **Modal account** configured: `uv run modal setup`
2. **Modal secret** named `LITELLM` with DSPy env vars:
   ```bash
   modal secret create LITELLM \
     DSPY_LM_MODEL=openai/gemini-3-flash-preview \
     DSPY_LM_API_BASE=https://your-proxy \
     DSPY_LLM_API_KEY=sk-... \
     DSPY_LM_MAX_TOKENS=65536
   ```
3. **Local `.env`** at project root with the same vars (for the planner LM).
4. **Dependencies synced**: `uv sync`

---

## Quick Mode — CLI One-Liner

For standard long-context tasks, use the CLI directly:

```bash
# Analyze a document
uv run fleet-rlm run-long-context \
  --docs-path <FILE> \
  --query "<QUERY>" \
  --mode analyze \
  --max-iterations 30 \
  --max-llm-calls 50 \
  --timeout 900

# Summarize a document with focus
uv run fleet-rlm run-long-context \
  --docs-path <FILE> \
  --query "<FOCUS_TOPIC>" \
  --mode summarize \
  --timeout 900

# With persistent volume
uv run fleet-rlm run-long-context \
  --docs-path <FILE> \
  --query "<QUERY>" \
  --mode analyze \
  --volume-name rlm-volume-dspy
```

All `run-*` commands support `--max-iterations`, `--max-llm-calls`, `--verbose`,
`--timeout`, `--secret-name`, `--volume-name`, and `--full-output`. Run
`uv run fleet-rlm --help` for full details.

---

## Interactive Mode — Custom Workflows with ModalInterpreter

For multi-step or custom workflows, use `ModalInterpreter` directly:

```python
from fleet_rlm import ModalInterpreter

with ModalInterpreter(
    timeout=600,
    volume_name='rlm-volume-dspy',
) as interp:
    import pathlib
    content = pathlib.Path('rlm_content/dspy-knowledge/dspy-doc.txt').read_text()
    result = interp.execute(
        'print(f"Loaded {len(content):,} chars")',
        variables={'content': content},
    )
    print(result)
```

### Scout the Content

Once `content` is in the sandbox, use the injected sandbox-side helpers:

```python
# See first 3000 chars
result = interp.execute("print(peek(content, 0, 3000))")

# Find all mentions of "optimizer"
result = interp.execute("matches = grep(content, 'optimizer', context=1); print(len(matches))")

# Split into sections
result = interp.execute("""
sections = chunk_by_headers(content)
for i, s in enumerate(sections):
    print(f"{i}: {s['header'][:60]}  ({len(s['content'])} chars)")
""")
```

### Chunk and Write to Filesystem

Write chunks to `/tmp/chunks/` (ephemeral) or `/data/chunks/` (volume-persisted):

```python
result = interp.execute("""
import os, json

chunks = chunk_by_size(content, 8000, 400)
os.makedirs('/tmp/chunks', exist_ok=True)

manifest = []
for i, chunk in enumerate(chunks):
    path = f'/tmp/chunks/chunk_{i:04d}.txt'
    with open(path, 'w') as f:
        f.write(chunk)
    manifest.append({'id': f'chunk_{i:04d}', 'path': path, 'chars': len(chunk)})

SUBMIT(chunk_count=len(manifest), manifest=manifest)
""")
```

### Subcall Loop (rlm-subcall subagent)

For each chunk, invoke the `rlm-subcall` subagent:

```
Subagent: rlm-subcall
Input:
  chunk_path: /tmp/chunks/chunk_0001.txt
  query: "What modules does DSPy provide?"
  chunk_id: chunk_0001
```

The subagent returns structured JSON with `relevant`, `missing`, and
`suggested_queries` fields. Collect all results, then synthesize.

### Synthesize in the Sandbox

```python
result = interp.execute("""
import json

findings = []
for r in all_results:
    for item in r.get('relevant', []):
        if item['confidence'] in ('high', 'medium'):
            findings.append(item)

seen = set()
unique = [f for f in findings if f['point'] not in seen and not seen.add(f['point'])]

SUBMIT(findings=unique, total=len(unique))
""", variables={'all_results': all_results})
```

---

## Full RLM Mode — dspy.RLM with ModalInterpreter

For fully automated RLM execution (the LLM writes its own code):

```python
import dspy
from fleet_rlm import ModalInterpreter, AnalyzeLongDocument

with ModalInterpreter(timeout=900, volume_name='rlm-volume-dspy') as interp:
    rlm = dspy.RLM(
        signature=AnalyzeLongDocument,
        interpreter=interp,
        max_iterations=20,
        max_llm_calls=30,
        verbose=True,
    )
    result = rlm(
        document=open('rlm_content/dspy-knowledge/dspy-doc.txt').read(),
        query="What are the main design decisions?",
    )
    print(f"Findings: {result.findings}")
    print(f"Answer: {result.answer}")
```
