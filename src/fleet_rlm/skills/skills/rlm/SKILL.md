---
name: rlm
description: "Hub for fleet_rlm: when to use dspy.RLM, turn budgets, progressive skills, and which workflow skill to load next."
---

# fleet_rlm RLM Hub

Canonical DSPy module: https://dspy.ai/api/modules/RLM/

## Product surface (clean only)

- **Transport:** `POST /api/sessions/{session_id}/turns` Server-Sent Events (`RuntimeEvent`). No WebSocket execution path.
- **Engine:** host-owned `dspy.RLM` with signature `FleetRLMSignature` (`request -> answer`).
- **Interpreter:** Daytona `code_interpreter` via a short-lived lease (never delete the sandbox on release).
- **Scope:** deterministic local User and Workspace identifiers; no public auth headers.
- **Run environment:** explicit `FLEET_RUN_ENVIRONMENT=daytona|deno`; Daytona is default.

## When to use RLM

Use recursive code exploration when the answer needs slicing large inputs, multi-step Python, or tool-mediated reads — not a single full-context LM call.

Clean turns always run through the RLM runner (no EscalatingFleet / ReAct route table).

## REPL contract

- Large fields are REPL variables; explore with `print()` and Python.
- Built-ins from DSPy RLM: `llm_query`, `llm_query_batched`, `SUBMIT(...)` (field names must match the signature — clean uses `answer`).
- Host tools (when bound): progressive `load_skill` / `read_skill_resource` (by **skill UUID**, not volume name); `read_attachment` / `create_artifact`.
- Do **not** assume live helpers: `sub_rlm`, `remember`/`recall`, WebSocket batch concurrency, or `fleet_rlm.runtime.*`.

## Budgets (`RunBudget`)

| Limit | Default | Maps to |
|-------|---------|---------|
| `max_iterations` | 20 | `dspy.RLM(..., max_iterations=...)` (not `max_iters`) |
| `max_llm_calls` | 50 | Semantic sub-LM calls |
| `max_output_chars` | 10_000 | Truncation of emitted text |
| `max_wall_seconds` | 300 | Host wall clock |
| `max_skill_loads` | 8 | Progressive skill loads per turn |
| `max_tool_calls` | 32 | Host tool invocations |

## Route to a workflow skill

1. Sandbox / interpreter / volume layout → `sandbox-execution` then `volume-bootstrap`
2. Large documents / variable mode → `long-context`
3. Failures / env / auth → `diagnostics`
4. JS-heavy pages (optional) → `browser-interaction`

Skill cards are metadata-only. Load full instructions with host `load_skill(skill_id)` after discovering ids from the turn’s skill card list.

## See also

- Architecture map: `references/architecture.md`
