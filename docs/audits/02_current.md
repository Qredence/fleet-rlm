# Fleet RLM Refactor — What Is Currently Happening

## Current phase

Fleet is currently positioned at the transition from Phase 5 to Phase 6.

Phase 5 is complete and committed. The current active work should be Phase 6 foundation:

```text
Phase 6 — Trace, Transcript, Performance, and MLflow
```

## Immediate working state

The backend has a complete Phase 5 substrate:

- FastAPI transport seam.
- Runtime execution backend seam.
- Opt-in direct RLM path.
- Legacy runtime fallback.
- Daytona facade package.
- First-class Skills package.
- Tool registry and policy gates.
- Artifact storage and controlled artifact tools.
- Attachment upload and chat attachment refs.
- AttachedFiles context for RLM.

## Current focus

The current focus is to make trace, transcript, performance, and MLflow handling canonical across both runtime backends.

Phase 6 should start with foundation work, not with frontend UI, config migration, GEPA optimization, or default-backend switching.

## What can be temporarily broken

Temporary local app usability breakage is acceptable during active implementation if it helps complete the refactor cleanly.

Allowed during the implementation pass:

- frontend app may not run;
- `/api/chat` may be temporarily broken;
- some tests may fail mid-branch;
- OpenAPI may drift before final sync;
- imports may fail during active module movement.

Not allowed in the final committed state without explicit approval:

- broken `POST /api/chat` contract;
- broken WebSocket execution path;
- broken `RuntimeEvent` compatibility;
- broken trace/performance response contracts;
- `ActiveSkills` or `load_skill` regression;
- MLflow required for default tests;
- raw provider/runtime errors leaked to clients.

## Current implementation stance

Use an aggressive internal refactor stance if needed, but keep public contracts restored by the final validation gate.

Preferred approach:

```text
create seam -> preserve behavior -> migrate one runtime concern -> validate -> repeat
```

## Current branch hygiene

Before Phase 6 implementation is accepted, remaining Phase 5 cleanup and hook-fix changes should be committed, dropped, or explicitly called out.

Recommended current branch sequence:

1. Commit or drop remaining Phase 5 cleanup/checkpoint edits.
2. Commit or drop `llm_query_batched` stability fixes separately from architecture docs.
3. Confirm branch is clean enough for Phase 6.
4. Start Phase 6 foundation.

## Current Phase 6 starting point

Phase 6 should begin by auditing existing trace, MLflow, runtime event, run-step, and performance-summary surfaces, then creating canonical modules only where needed.

Likely existing surfaces to inspect:

- runtime events;
- SSE projection;
- WebSocket projection;
- run lifecycle persistence;
- session trace debug APIs;
- performance summary schemas;
- existing MLflow span events;
- existing quality/GEPA utilities.

## Current non-goals

Do not start the following during the initial Phase 6 foundation slice:

- Phase 7 typed config/config.yaml;
- Phase 8 GEPA quality lane;
- Phase 9 direct RLM default switch;
- Phase 10 frontend SSE migration;
- broad legacy runtime deletion;
- MLflow server requirement for tests or local dev.
