---
name: rlm
description: Translate fleet-rlm's shared ReAct plus dspy.RLM runtime into Claude Code workflows. Use when you need a Claude-facing mental model for fleet-rlm, especially to choose between modal_chat and daytona_pilot, run the local server surfaces, or plan long-context/runtime work.
---

# RLM — Claude Code Translation Layer

Use this skill as the Claude Code view of `fleet-rlm`. It is not a thin wrapper
around `.claude/`; it is the packaged explanation of how the project actually
works today.

## Core Model

- `fleet-rlm` exposes one shared conversational runtime built on ReAct plus `dspy.RLM`.
- `modal_chat` is the default product path.
- `daytona_pilot` is the Daytona-backed variant of the same runtime, not a separate chat stack.
- The live product surfaces are `Workbench`, `Volumes`, and `Settings`.

If the task is about the Daytona workbench path, also load `daytona-runtime`.

## Canonical Commands

```bash
# from repo root
uv sync --all-extras --dev
uv run fleet web
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm serve-mcp --transport stdio
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

## Runtime Translation

### `modal_chat`

- Default workspace mode
- Modal remains the sandbox/interpreter backend
- Request-side `execution_mode` only applies here

### `daytona_pilot`

- Same ReAct plus `dspy.RLM` orchestration core
- Daytona is the interpreter/sandbox backend
- Use request controls `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
- Persistent Daytona workspace memory lives on the mounted volume at `/home/daytona/memory`

## Claude Code Usage

Use the scaffold as an alternative operating surface for `fleet-rlm`:

- Load this skill when you need to map a user request onto the fleet runtime model
- Delegate long-context orchestration to `rlm-orchestrator`
- Delegate runtime or integration debugging to `rlm-specialist`
- Delegate Modal-only setup failures to `modal-interpreter-agent`
- Delegate leaf chunk analysis to `rlm-subcall`

## Practical Rules

- Prefer `fleet web` for local product work and `fleet-rlm serve-api` when you need the backend surface explicitly.
- Treat `openapi.yaml`, websocket payloads, and runtime mode wiring as contract surfaces.
- Do not treat Daytona as a separate agent architecture; it is a different interpreter backend on the same runtime.
- For PDFs and binary docs, prefer the ReAct document tools (`load_document`, `read_file_slice`) instead of raw `read_text()`.

## When To Reach For Other Skills

- `daytona-runtime` for Daytona-specific execution, workspace volume, and smoke-test guidance
- `rlm-debug` for failure diagnosis and contract debugging
- `modal-sandbox` for Modal-only sandbox lifecycle or volume management
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
