---
name: rlm
description: Translate fleet-rlm's shared ReAct plus dspy.RLM runtime into Claude Code workflows. Use when you need a Claude-facing mental model for fleet-rlm, especially for daytona_pilot execution, running the local server surfaces, or planning long-context/runtime work.
---

# RLM — Claude Code Translation Layer

Use this skill as the Claude Code view of `fleet-rlm`. It is not a thin wrapper
around `.claude/`; it is the packaged explanation of how the project actually
works today.

## Core Model

- `fleet-rlm` exposes one shared conversational runtime built on ReAct plus `dspy.RLM`.
- `daytona_pilot` is the primary runtime path. Daytona is the interpreter/sandbox backend.
- The live product surfaces are `Workbench`, `Volumes`, and `Settings`.

## Canonical Commands

```bash
# from repo root
uv sync --all-extras
uv run fleet web
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm serve-mcp --transport stdio
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

## Runtime — `daytona_pilot`

- Daytona is the interpreter/sandbox backend on the shared ReAct + `dspy.RLM` backbone.
- Request controls: `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`.
- Durable volume rooted at `/home/daytona/memory`; canonical dirs: `memory/`, `artifacts/`, `buffers/`, `meta/`.
- The live workspace is transient; only the durable volume persists across sessions.
- Run `fleet-rlm daytona-smoke` before using `daytona_pilot` in the workspace.

## Claude Code Usage

Use the scaffold as an alternative operating surface for `fleet-rlm`:

- Load this skill when you need to map a user request onto the fleet runtime model
- Delegate long-context orchestration to `rlm-orchestrator`
- Delegate runtime or integration debugging to `rlm-specialist`
- Delegate leaf chunk analysis to `rlm-subcall`

## Practical Rules

- Prefer `fleet web` for local product work and `fleet-rlm serve-api` when you need the backend surface explicitly.
- Treat `openapi.yaml`, websocket payloads, and runtime mode wiring as contract surfaces.
- Daytona is the interpreter backend, not a separate orchestration system.
- For PDFs and binary docs, prefer the ReAct document tools (`load_document`, `read_file_slice`) instead of raw `read_text()`.

## When To Reach For Other Skills

- `daytona-runtime` for Daytona-specific execution, workspace volume, and smoke-test guidance
- `rlm-debug` for failure diagnosis and contract debugging
- `rlm-subcall` for leaf chunk analysis. Invoke with a dict like:

```yaml
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

## Full RLM Mode — dspy.RLM with DaytonaInterpreter

For fully automated RLM execution (the LLM writes its own code):

```python
import dspy
from fleet_rlm.runtime.config import configure_planner_from_env
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter
from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument

configure_planner_from_env()

interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    volume_name="rlm-volume-dspy",
    timeout=900,
)
interp.start()
try:
    rlm = dspy.RLM(
        signature=SummarizeLongDocument,
        interpreter=interp,
        max_iterations=20,
        max_llm_calls=30,
        verbose=True,
    )
    result = rlm(
        document=open('rlm_content/dspy-knowledge/dspy-doc.txt').read(),
        focus="What are the main design decisions?",
    )
    print(f"Key Points: {result.key_points}")
    print(f"Summary: {result.summary}")
finally:
    interp.shutdown()
```
