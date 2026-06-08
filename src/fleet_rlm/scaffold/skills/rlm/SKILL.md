---
name: rlm
description: "Hub skill for fleet-rlm: when to use dspy.RLM vs ReAct/CodeAct and which workflow skill to load next."
---

# fleet-rlm RLM Hub

Canonical DSPy module: https://dspy.ai/api/modules/RLM/

Module variants overview: https://dspy.ai/diving-deeper/built-in-module-variants/

## When to use `dspy.RLM`

Use RLM when context is too large, unevenly relevant, or benefits from programmatic exploration — not a single full-context LM call.

fleet-rlm routes to `dspy.RLM` when:

- `execution_mode` is `rlm` / `rlm_only`
- Auto mode detects URL document analysis (`url_document_rlm`)
- Auto mode detects large context (`large_context_rlm`, threshold via `FLEET_RLM_LARGE_CONTEXT_THRESHOLD`)

Lightweight chat stays on ChainOfThought; tool-heavy turns use ReAct via `[TOOLS NEEDED]` sentinel.

## REPL contract (Daytona interpreter)

- Large fields are REPL variables; explore with `print()` and Python.
- Built-ins: `llm_query`, `llm_query_batched`, `SUBMIT(...)`.
- Fleet extensions: `sub_rlm`, `sub_rlm_batched`, volume `load_skill(name)`.
- Durable storage: `/home/daytona/memory/` (`skills/system/*.md` seeded from scaffold).

## Route to a workflow skill

1. Sandbox / REPL / SUBMIT / volume lifecycle → `sandbox-execution`
2. Recursive child sandboxes / budgets → `delegation`
3. Signatures / modules / execution modes → `dspy-programs`
4. Large documents / variable mode → `long-context`
5. GEPA / offline optimization → `optimization`
6. Failures / contract drift → `diagnostics`
7. Volume layout / CRUD → `volume-bootstrap`
8. Playwright / rendered pages → `browser-interaction`
