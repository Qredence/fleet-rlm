---
name: rlm
description: "Use when starting fleet-rlm agent work or routing a task to the right workflow skill for sandbox execution, recursive delegation, signature design, optimization, long-context work, or debugging."
---

## Core Model

AgentRuntime receives a user turn and resolves available tools.
EscalatingFleetModule decides execution path: ChainOfThought (lightweight) OR dspy.RLM (heavy).
The chosen path produces tool calls including delegate_to_rlm for recursive work.
Tools execute via DaytonaInterpreter which manages sandbox lifecycle.
DaytonaInterpreter wraps a DaytonaSandboxRuntime (SDK, snapshots, volumes).
Code runs inside an isolated Daytona workspace with durable volume at /home/daytona/memory/.
Results return via SUBMIT() protocol back through the interpreter.
The module aggregates tool results and streams the final response.
Budget tracking (max_llm_calls, timeouts) is enforced at interpreter and module level.
WebSocket streaming delivers incremental output to the Web UI or API consumers.

## Canonical Commands

- `uv run fleet web` — Web UI on 0.0.0.0:8000
- `uv run fleet-rlm serve-api --port 8000` — API server
- `uv run fleet-rlm chat` — Terminal interactive chat
- `uv run fleet-rlm daytona-smoke --repo <url>` — Validate Daytona
- `uv run fleet-rlm optimize` — Run GEPA/MIPROv2 optimization

## Decision Tree

1. EXECUTE code in sandbox / persist results / manage workspace → `sandbox-execution`
2. DELEGATE recursive work (parent→child, batch, budget) → `delegation`
3. DESIGN signatures / choose modules / wire execution modes → `dspy-programs`
4. PROCESS large documents or codebases (chunking, map-reduce) → `long-context`
5. OPTIMIZE programs (GEPA, evaluation, datasets, MLflow) → `optimization`
6. DIAGNOSE failures (sandbox errors, contract drift, test failures) → `diagnostics`
7. UNDERSTAND volume filesystem / what's pre-initialized / CRUD contract → `volume-bootstrap`
