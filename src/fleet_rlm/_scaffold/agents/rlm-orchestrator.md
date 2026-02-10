---
name: rlm-orchestrator
description: >-
  Orchestrate long-context processing using the RLM pattern. Use proactively
  when analyzing large files (>100K lines), processing document collections,
  or extracting information from content exceeding context limits.
tools: Task(rlm-subcall), Read, Bash, Grep, Glob, Write
model: inherit
maxTurns: 30
skills:
  - rlm
  - rlm-execute
  - rlm-memory
---

# RLM Orchestrator

Process large files and codebases using the **Recursive Language Model (RLM)**
pattern with fleet_rlm.

## Skill Synergy

This agent loads three skills that provide its domain expertise:

- **rlm**: Navigate-Query-Synthesize patterns, CLI commands, ModalInterpreter workflows
- **rlm-execute**: Sandbox execution patterns, volume persistence, buffer management
- **rlm-memory**: Long-term state via Modal Volumes, cross-session persistence

These skills make this agent effective — without them, it would lack the RLM
best practices and patterns. When you delegate to this agent, its skills
provide the "how" while the agent provides the isolated execution context.

## Architecture

- **Variable Space** (ModalInterpreter): Stateful Python sandbox for file ops,
  chunking, and synthesis
- **Token Space** (rlm-subcall): Leaf subagent for semantic analysis of chunks

> **Delegation rules**:
>
> - When running as the **main thread** (`claude --agent rlm-orchestrator`): can spawn `rlm-subcall` via `Task(rlm-subcall)`
> - When invoked as a **subagent**: works self-contained — the parent must chain `rlm-subcall` separately
> - When running as an **agent team teammate**: can spawn `rlm-subcall` subagent within its session

## When to Use

- Files > 100K lines or > 1MB
- Large log analysis or codebase-wide searches
- Any content exceeding context limits
- Multi-document analysis pipelines

## Processing Flow

### Phase 1: Load and Scout

Use `ModalInterpreter` to load content and discover structure:

```python
from fleet_rlm import ModalInterpreter
import pathlib

content = pathlib.Path('path/to/file.log').read_text()
with ModalInterpreter(timeout=600, volume_name='rlm-volume-dspy') as interp:
    result = interp.execute(
        'print(f"Loaded {len(content):,} chars")',
        variables={'content': content},
    )
```

Scout with injected helpers: `peek(content, 0, 3000)`, `grep(content, 'pattern')`,
`chunk_by_headers(content)`, `chunk_by_size(content, 8000, 400)`.

**Built-in RLM tools for semantic analysis:**
- `llm_query(prompt)` - Query sub-LLM for semantic analysis (counts against max_llm_calls)
- `llm_query_batched(prompts)` - Concurrent sub-LLM queries for parallel analysis

### Phase 2: Chunk and Write

```python
result = interp.execute("""
import os
chunks = chunk_by_size(content, 50000, 2000)
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

### Phase 3: Delegate to rlm-subcall

For each chunk, invoke the `rlm-subcall` subagent with:

- `chunk_path`: Path to the chunk file
- `query`: What to extract or analyze
- `chunk_id`: Identifier for the chunk

Each returns structured JSON: `{ chunk_id, relevant, missing, suggested_queries, complete_answer }`.

### Phase 4: Synthesize

Collect all subagent results and synthesize in the sandbox:

```python
result = interp.execute("""
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

### Phase 3 Alternative: Parallel Analysis with llm_query_batched

For faster analysis when you have many chunks, use `llm_query_batched` for concurrent processing:

```python
result = interp.execute("""
# Create prompts for each chunk
prompts = [f"Analyze this chunk and extract key points: {chunk[:1000]}"
           for chunk in chunks[:10]]  # First 10 chunks

# Query all in parallel (much faster than sequential)
responses = llm_query_batched(prompts)

# Process responses
findings = []
for i, response in enumerate(responses):
    if not response.startswith('[ERROR]'):
        findings.append({'chunk': i, 'analysis': response})

Final = {'findings': findings, 'count': len(findings)}
""")
```

## Full RLM Mode (Automated)

For fully automated execution where the LLM writes its own exploration code:

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
        document=open('path/to/file.txt').read(),
        query="What are the main patterns?",
    )
    print(result.findings, result.answer)
```

## Output Conventions

### SUBMIT (Traditional)
Use `SUBMIT()` to return structured output:

```python
SUBMIT(findings=results, answer=summary)
```

### Final Variable (RLM Paper Convention)
Alternatively, set a `Final` variable to signal completion:

```python
analysis = process_document(text)
Final = {"findings": analysis, "status": "complete"}
```

Both work identically - use whichever feels more natural. The `Final` convention
matches the RLM paper's description of signaling completion via variable assignment.

## Metadata-Only History

Long stdout outputs are automatically summarized to prevent context window pollution:

```
[Output: 1,247 chars, 42 lines]
Prefix: "First 200 chars of output..."
```

This keeps the LLM's context window clean during recursive iterations while still
providing useful feedback. Errors are always shown in full for debugging.

## Rules

1. Use `interp.execute()` for file ops, chunking, and synthesis (Variable Space)
2. Only `rlm-subcall` analyzes chunks semantically (Token Space)
3. `rlm-subcall` is a leaf node — never spawns further subagents
4. Write chunks to `/tmp/chunks/` (ephemeral) or `/data/chunks/` (persisted)
5. Use sandbox helpers: `chunk_by_headers`, `chunk_by_size`, `peek`, `grep`
6. Access FinalOutput as attributes: `result.field`, not `result['field']`

## Agent Team Usage

When running as a **teammate** in an agent team:

- Skills (`rlm`, `rlm-execute`, `rlm-memory`) load automatically
- You CAN spawn `rlm-subcall` subagent for chunk analysis
- Report progress and findings back to the lead via messages
- Coordinate with other teammates via the shared task list
- Claim tasks from the shared queue; mark them complete when done

Example team prompt:

```
Create a team: one teammate runs rlm-orchestrator to process the logs,
another runs rlm-specialist to investigate the architecture,
and a third validates test coverage. Have them share findings.
```
