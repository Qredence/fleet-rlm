# Fleet RLM Backend Refactor — Markdown File Index

Use these files as project knowledge / implementation context for GPT-5.6 Terra Max.

## Rationale and goals

```text
00_backend_refactor_goal.md
01_why_this_refactor.md
02_why_sse.md
03_why_focus_rlm_mode.md
04_implementation_rationale_and_sequence.md
05_phase6_rationale.md
06_final_goal_statement_for_terra_max.md
```

## How to use

Read in this order:

1. `00_backend_refactor_goal.md`
2. `01_why_this_refactor.md`
3. `02_why_sse.md`
4. `03_why_focus_rlm_mode.md`
5. `04_implementation_rationale_and_sequence.md`
6. `05_phase6_rationale.md`
7. `06_final_goal_statement_for_terra_max.md`

Then use the phase implementation prompts for Phase 6, Phase 7, Phase 8, Phase 9, and Phase 10.

## Key reminders

```text
- Phase 5 is complete.
- Phase 6 is next.
- SSE is canonical for chat transcript streaming.
- WebSocket remains for bidirectional/control compatibility.
- direct RLM is the target runtime, but not default until parity.
- MLflow is observability/quality infrastructure, not required local-dev infrastructure.
- GEPA belongs in the quality lane, not normal /api/chat.
```
