# Fleet RLM backend foundation plan

This directory preserves the accepted plan and evidence used to rebuild the
Fleet RLM backend. The result now lives at canonical `src/fleet_rlm/`.

It does not preserve migration-era runtime parity, WebSocket transcript compatibility, backend-selection machinery, or the `DirectRLMRunner` name. The existing parent-directory dossiers remain historical migration evidence; this clean-backend package is the source of truth for a greenfield implementation.

Pre-cutover file paths in the detailed tickets are historical evidence. Active
implementation and tests use `src/fleet_rlm/` and `tests/*/backend/`.

## Product kernel

```text
Chat UI
  -> FastAPI POST /api/chat
  -> Server-Sent Events
  -> fresh per-turn dspy.RLM
  -> capable root LM + smaller sub-LM
  -> DaytonaInterpreter
  -> Daytona Sandbox with mounted Daytona Volume
  -> durable session checkpoint, Skills, inputs, and artifacts
```

Fleet RLM has three permanent backend foundations:

1. **DSPy, especially `dspy.RLM`** - the primary open-ended reasoning and code-driven execution engine.
2. **FastAPI SSE** - the canonical user-facing chat transport.
3. **Daytona Sandbox and Volume** - isolated execution plus durable filesystem-native agent state.

Everything else must operate this loop, make it useful, harden it, or improve it later.

## Files

```text
docs/plan-implementation/clean-backend/
├── README.md
├── wayfinder.md
├── to-spec.md
├── codebase-design.md
├── to-tickets.md
├── code-review.md
└── context7-contracts.md
```

- [Wayfinder](wayfinder.md): navigation, priority, ownership, and decision rules.
- [Product specification](to-spec.md): product and behavioral requirements.
- [Codebase design](codebase-design.md): source tree, module ownership, interfaces, and data flow.
- [Ticket plan](to-tickets.md): ordered TDD delivery plan and phase gates.
- [Code-review rubric](code-review.md): severity policy and blocker ownership.
- [Framework contracts](context7-contracts.md): verified DSPy, Daytona, and FastAPI facts.

## Reading order

1. `wayfinder.md`
2. `to-spec.md`
3. `codebase-design.md`
4. `context7-contracts.md`
5. `to-tickets.md`
6. `code-review.md`

## Delivery phases

| Phase | Outcome | Foundation? |
|---|---|---|
| 1. RLM/SSE/Daytona kernel | A real SSE request executes a real `dspy.RLM` in Daytona | yes |
| 2. Stateful session runtime | History and durable files survive API and Sandbox lifecycle transitions | yes |
| 3. Progressive capabilities | The RLM loads Skills, reads attachments, and creates artifacts | yes |
| 4. Production hardening | Authentication, budgets, cancellation, observability, recovery, and promotion evidence | yes |
| 5. Long-context memory | Summaries and governed durable memory retrieval scale long sessions | later |
| 6. Self-improvement | Memory consolidation, Skill evolution, and GEPA operate offline with promotion gates | later |

The backend foundation is complete after Phase 4. Phases 5 and 6 expand a stable system; they must not delay the first operational RLM session.

## Global invariants

- `dspy.RLM` is the primary open-ended runtime, not an optional feature behind a generic agent abstraction.
- The application class is `RLMRunner`; there is no `DirectRLMRunner` or `RLMAgent` wrapper.
- A fresh `dspy.RLM` is created for every concurrent turn.
- The root LM controls the recursive trajectory; `sub_lm` handles bounded semantic subqueries.
- Generated Python and approved Skill scripts execute in Daytona, never in FastAPI.
- A Daytona Volume is mounted into the Sandbox for durable filesystem-native content.
- Interpreter variables are an optimization, never the sole durable copy of session state.
- FastAPI SSE is the only transcript transport in the clean backend.
- The client cannot select model providers, Sandbox IDs, Volume paths, or privileged Skills.
- Deterministic Python owns identity, authorization, validation, budgets, persistence, path safety, and redaction.
- Raw provider exceptions, credentials, private paths, and hidden reasoning never enter public SSE events.
- Memory consolidation, generated Skills, and GEPA stay outside normal chat turns.

## Status vocabulary

- `planned`: no accepted implementation evidence exists.
- `in_progress`: implementation exists, but the phase gate has not passed.
- `blocked`: a named dependency or failed criterion prevents closure.
- `complete`: committed implementation and all required evidence exist.
- `promotion_gated`: implementation exists but cannot become the production default until the named live gate passes.

No phase advances from prose. Status is subordinate to current code, tests, generated contracts, and live evidence.

## Foundation completion flow

The clean backend is production-foundational only when a live end-to-end scenario proves:

1. The Chat UI opens an authenticated SSE turn through `POST /api/chat`.
2. Fleet restores the session's `dspy.History`.
3. Fleet acquires or resumes a Daytona Sandbox with the workspace Volume mounted.
4. `RLMRunner` creates a fresh `dspy.RLM`.
5. The capable root LM generates Python.
6. The Python executes in Daytona.
7. The generated program invokes the smaller `sub_lm` through `llm_query` or `llm_query_batched`.
8. The RLM loads one authorized Skill.
9. The RLM reads one attachment by ID.
10. The RLM creates one durable artifact on the mounted Volume.
11. RuntimeEvents stream to the UI with exactly one terminal event.
12. The turn and checkpoint commit transactionally.
13. The Sandbox is stopped, paused, archived, or replaced according to provider capability.
14. A later turn reconstructs the session without relying on Python globals.
15. The later answer demonstrably uses prior history and the existing artifact.

## Source-of-truth order

When planning files disagree, apply this order:

1. Locked framework contract tests.
2. `to-spec.md` behavioral requirements.
3. `codebase-design.md` ownership and interface rules.
4. `to-tickets.md` task and phase gates.
5. Historical migration dossiers.

Update `context7-contracts.md` whenever DSPy, Daytona, FastAPI, Starlette, or Pydantic versions change.
