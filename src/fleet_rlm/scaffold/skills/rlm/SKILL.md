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
- The live product surfaces are `Workbench`, `Volumes`, `Optimization`, and `Settings`.
- The top-level chat entry point is `FleetAgent` at `runtime/agent/agent.py` (a thin `dspy.ReAct` wrapper). The recursive engine is `dspy.RLM` built in `runtime/models/builders.py` and exercised via `runtime/tools/rlm_delegate.py`.

## Canonical Commands

```bash
# from repo root
uv sync --all-extras
uv run fleet web
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

## Runtime — `daytona_pilot`

- Daytona is the interpreter/sandbox backend on the shared ReAct + `dspy.RLM` backbone.
- Request controls: `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`.
- Durable volume rooted at `/home/daytona/memory`; canonical dirs: `memory/`, `artifacts/`, `buffers/`, `meta/`.
- The live workspace is transient; only the durable volume persists across sessions.
- Run `fleet-rlm daytona-smoke` before using `daytona_pilot` in the workspace.

## Claude Code Usage

Load this skill when you need to map a user request onto the fleet-rlm runtime model. Pair with the sibling skills below for specific tasks:

- `daytona-runtime` — Daytona execution, volume layout, smoke validation
- `rlm-execute` — running Python in a Daytona sandbox with durable persistence
- `rlm-long-context` — processing documents that exceed a single context window
- `rlm-batch` / `rlm-memory` / `rlm-debug` — batched recursive work, session memory, failure diagnosis

## Practical Rules

- Prefer `fleet web` for local product work and `fleet-rlm serve-api` when you need the backend surface explicitly.
- Treat `openapi.yaml`, websocket payloads, and runtime mode wiring as contract surfaces.
- Daytona is the interpreter backend, not a separate orchestration system.
- For PDFs and binary docs, prefer the ReAct document tools (`load_document`, `read_file_slice`) instead of raw `read_text()`.

## When To Reach For Other Skills

- `daytona-runtime` for Daytona-specific execution, workspace volume, and smoke-test guidance
- `rlm-debug` for failure diagnosis and contract debugging
- `rlm-long-context` for leaf-chunk decomposition of a document that exceeds a single context

## Full RLM Mode — dspy.RLM with DaytonaInterpreter

For fully automated RLM execution (the LLM writes its own code):

```python
import dspy
from fleet_rlm.runtime.config import configure_planner_from_env
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
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
