# Fleet RLM implementation phases

This directory is the canonical implementation-planning module for the Fleet
RLM backend refactor. Its destination is an RLM-native FastAPI/SSE backend with
`dspy.RLM` as the primary agentic runtime, Daytona as the generated-code
execution substrate, and progressively disclosed Skills and policy-controlled
tools. Start here for phase order, status, shared rules, and links to the
detailed phase dossiers.

## Reading order

1. Read [Target architecture](target-architecture.md) for the intended final
   module ownership and migration rules.
2. Open the current phase dossier from the ordered list below.
3. Read linked evidence when changing a contract or closing acceptance items.
4. Consult the canonical [architecture decision records](../adr/) for decisions;
   ADRs are linked, not copied into phase dossiers.

The current product phase is [Phase 8 — GEPA quality](phases/08-gepa-quality/README.md)
(status `partial`). [Phase 8.5 — Persistence DB](phases/08.5-persistence-db/README.md)
is planned structural work that may run in parallel when cheap and **must not
gate** promotion. Direct RLM remains
[promotion-gated in Phase 9](phases/09-direct-rlm-promotion/README.md).

## Status vocabulary

- `complete`: committed implementation and its required validation evidence exist.
- `partial`: useful implementation exists, but named work remains intentionally open.
- `in_progress_uncommitted`: implementation evidence exists only in the current
  working tree and must not be presented as shipped.
- `planned`: sequenced work that has not started or is intentionally deferred.
- `promotion_gated`: implementation may exist, but a live evidence gate must pass
  before the behavior can become the default.

Phase status advances only after its acceptance evidence passes. A roadmap or
audit statement is a contract, not proof of implementation.

Historical status is subordinate to current evidence. If live code, generated
contracts, or the required validation lane disproves a completed acceptance
claim, preserve the historical commit reference but change the current phase or
subphase status to `partial` or `in_progress_uncommitted` and add a named,
unchecked remediation criterion.

## Implementation order

1. [Phase 1 — SSE transport](phases/01-sse-transport/README.md) — complete.
2. [Phase 2 — Direct RLM runtime](phases/02-direct-rlm-runtime/README.md) — complete.
3. [Phase 3 — Skills](phases/03-skills/README.md) — complete overall; Phase 3F partial.
4. [Phase 4 — Daytona facade](phases/04-daytona-facade/README.md) — complete.
5. [Phase 5 — Tools, artifacts, attachments](phases/05-tools-artifacts-attachments/README.md) — complete.
6. [Phase 6 — Observability](phases/06-observability/README.md) — complete.
7. [Phase 7 — Typed config](phases/07-typed-config/README.md) — complete in `f61fd045`.
8. [Phase 8 — GEPA quality](phases/08-gepa-quality/README.md) — partial.
8.5. [Phase 8.5 — Persistence DB package](phases/08.5-persistence-db/README.md) — planned (structural; non-blocking for Phase 9).
9. [Phase 9 — Direct RLM promotion](phases/09-direct-rlm-promotion/README.md) — promotion gated.
10. [Phase 10 — Frontend SSE and cleanup](phases/10-frontend-sse-cleanup/README.md) — planned.

## Dependency path

```text
Product critical path:
Phase 6 observability
  -> Phase 7 typed configuration
  -> Phase 8 offline GEPA quality (product)
  -> Phase 9 direct-RLM promotion evidence and default switch
  -> Phase 10 frontend SSE adoption and evidence-backed legacy cleanup

Structural side path (non-blocking for Phase 9):
Phase 8.5A model registry + db package move + re-exports
  -> Phase 8.5B optional tiny model domain merge (ops.py)
  parallel after Phase 8 schema freeze, or between 8 and 9 only if cheap
```

Completed phases remain prerequisites where their public contracts are consumed.
A reopened phase blocks dependent promotion or deletion work until its named
remediation evidence passes.

## Global invariants

Every phase follows the migration loop:

```text
create seam -> preserve behavior -> migrate one runtime concern -> validate -> repeat
```

Every dossier must state one purpose, explicit non-goals, compatibility rules,
acceptance criteria, and validation evidence. Across all phases:

- `POST /api/chat`, WebSocket control, `RuntimeEvent`, Skills, Daytona session
  state, attachments, artifacts, and trace schemas remain compatible until a
  later phase explicitly retires them with evidence.
- `legacy_agent_runtime` remains available until direct RLM promotion succeeds.
- Backend selection remains server-side and is not accepted on `ChatRequest`.
- Custom-interpreter `dspy.RLM` instances are never shared across concurrent runs.
- Code and trusted scripts execute in Daytona, never on the FastAPI host.
- Client errors are sanitized; raw provider, credential, path, Daytona, and
  MLflow details remain server-side.
- MLflow is optional and disabled unless configured; default tests do not need it.
- GEPA and other optimization work remain outside normal `/api/chat` turns.
- Config work begins with the Phase 7 audit and has no import-time side effects.
- Postgres/Neon is the durable system of record; LocalStore remains a limited
  dual backend for dev/test and must not be treated as full Neon parity.
- Persistence package moves (Phase 8.5) are behavior-preserving, produce no
  intentional schema delta, and do not gate direct-RLM promotion.

## Maintaining the module

- Put phase status and acceptance evidence in the owning dossier, not a second ledger.
- Put phase-specific audit evidence beside the dossier under an `evidence-*.md` name.
- Keep ADRs in `docs/adr/` and link them from the relevant dossier.
- Each dossier must state purpose, prerequisites, stable interfaces or code-tree
  ownership, non-goals, acceptance criteria, validation lanes, rollback or
  compatibility conditions, and required evidence. Unknown details stay
  unchecked; they are never filled with invented evidence.
- After status changes, run `uv run python scripts/sync_plans_canvas.py` and
  `uv run python scripts/sync_plans_canvas.py --check`.
- Run `make check-docs` for any documentation or workflow change.

## Completion definition

The roadmap is complete when the target architecture's completion definition is
met with linked evidence: SSE is the normal transcript transport, direct RLM is
the promoted primary agentic runtime, Daytona contains generated-code and Skill
script execution, Skills/tools/files/artifacts are policy controlled, traces and
optional MLflow are backend owned, GEPA is offline, and Phase 10 has isolated or
removed legacy-only paths without losing required rollback or control surfaces.
