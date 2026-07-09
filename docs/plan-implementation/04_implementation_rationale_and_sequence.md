# Fleet RLM Backend Refactor — Implementation Rationale and Sequence

## Why the sequence matters

The roadmap is intentionally ordered to reduce rewrite risk.

The backend cannot safely jump straight to “direct RLM default” because the surrounding capabilities must exist first:

```text
transport seam
execution backend seam
direct RLM opt-in path
runtime event parity
skills subsystem
Daytona facade
tools/files/artifacts/attachments
trace/performance/MLflow
config audit
GEPA quality lane
default switch
frontend SSE migration
```

## Completed sequence through Phase 5

### Phase 1 — SSE transport seam

Purpose:

```text
Create /api/chat as a canonical SSE chat endpoint without removing WebSocket.
```

Rationale:

```text
A stable transport seam lets backend execution change without forcing frontend/runtime changes at the same time.
```

### Phase 2A — Execution backend seam

Purpose:

```text
Introduce ExecutionBackend behind stream_turn().
```

Rationale:

```text
The backend needs to choose legacy or direct RLM internally without exposing execution_backend in ChatRequest.
```

### Phase 2B/2C/2D — Direct RLM skeleton, golden path, event parity

Purpose:

```text
Prove direct RLM can run and emit RuntimeEvent-compatible output.
```

Rationale:

```text
Direct RLM cannot become default until it behaves like a backend-neutral RuntimeEvent source.
```

### Phase 3 — Skills

Purpose:

```text
Make skills first-class backend capability bundles.
```

Rationale:

```text
Direct RLM needs controlled, visible, scoped, resource-backed skills rather than ad-hoc markdown loading.
```

### Phase 4 — Daytona facade

Purpose:

```text
Create fleet_rlm.daytona.* as the stable sandbox substrate.
```

Rationale:

```text
Direct RLM, tools, files, artifacts, and skills should not depend on legacy integration paths directly.
```

### Phase 5 — Tools, Artifacts, Attachments

Purpose:

```text
Expose controlled capabilities to RLM through safe Daytona-backed tools and durable file/artifact contexts.
```

Rationale:

```text
A direct RLM without tools, files, attachments, and artifacts would only be a chat model. Fleet needs a sandboxed work engine.
```

## Remaining sequence

### Phase 6 — Trace, Transcript, Performance, and MLflow

Purpose:

```text
Make backend spans and trace data canonical across legacy and direct RLM.
```

Rationale:

```text
The system cannot safely switch default backend until both paths produce comparable traces, transcript categories, and performance summaries.
```

### Phase 7 — Config Audit and Typed Config

Purpose:

```text
Introduce typed process config without import side effects.
```

Rationale:

```text
Trace/MLflow/direct RLM settings should be known before config is frozen.
```

### Phase 8 — GEPA and Quality Lane

Purpose:

```text
Add quality optimization outside the normal chat hot path.
```

Rationale:

```text
GEPA should optimize programs/prompts using evaluation data, not run opportunistically inside user chat turns.
```

### Phase 9 — Direct RLM Default Switch

Purpose:

```text
Make direct RLM the default backend.
```

Rationale:

```text
Only after parity can Fleet safely promote the clean runtime path.
```

### Phase 10 — Backend Legacy Cleanup and Frontend SSE Handoff

Purpose:

```text
Reduce legacy surface and prepare/complete frontend SSE-first migration.
```

Rationale:

```text
Once direct RLM is default and SSE chat is canonical, old runtime/frontend coupling can be retired deliberately.
```

## Why not do Phase 6 before Phase 5

Trace systems should trace stable concepts.

Before Phase 5, artifact/file/tool/attachment concepts were still moving. Implementing Phase 6 first would have produced traces coupled to temporary abstractions.

## Why not do config earlier

Config too early would freeze names and scopes before the architecture clarified:

- backend execution settings;
- MLflow settings;
- GEPA settings;
- Daytona settings;
- tool exposure policy;
- trace recorder settings.

## Why not do GEPA earlier

GEPA needs:

- stable direct RLM execution;
- trace/performance data;
- optional MLflow integration;
- quality datasets;
- explicit promotion workflows.

Those depend on Phases 6 and 7.
