# Fleet RLM Backend Refactor — Goal Statement for Terra Max

## Mission

Complete the Fleet RLM backend refactor from the current post-Phase-5 state to a clean direct-RLM-ready backend.

The final backend should make direct `dspy.RLM` the default execution mode only after trace, performance, MLflow, config, and quality foundations are complete and validated.

## Explicit goal

Build a backend where:

```text
FastAPI handles transport and auth.
stream_turn() owns the execution seam.
direct RLM owns the future runtime path.
legacy AgentRuntime remains fallback.
Daytona owns sandbox execution.
Skills own task guidance/resources/scripts.
Tools expose controlled capabilities.
Files/Attachments provide safe user context.
Artifacts store durable generated outputs.
Observability/Traces own debug, transcript, performance, and MLflow integration.
Quality/GEPA runs outside the chat hot path.
```

## Explicit rationale

The refactor exists because Fleet is evolving from an agent-runtime prototype into a production backend for sandboxed, skill-aware, traceable RLM execution.

The system must support serious multi-step work while remaining:

- testable;
- observable;
- safe;
- policy-controlled;
- compatible with existing APIs;
- capable of fallback;
- ready for quality optimization.

## Why SSE

Use SSE for canonical chat because chat output is a server-to-client stream.

SSE should carry frontend-safe projected parts.

WebSocket should remain for bidirectional execution/control surfaces.

The final state is:

```text
/api/chat SSE = canonical chat transcript transport
WebSocket = terminal/live sandbox/control compatibility
```

## Why RLM

Focus on RLM because Fleet is not just a chat UI. It is a sandboxed work engine.

RLM is the correct center for:

- reasoning;
- tool use;
- code execution;
- skill application;
- attachment/file context;
- artifact generation;
- traceable trajectories;
- later GEPA optimization.

## What not to do

Do not:

- make direct RLM default before parity;
- remove legacy fallback early;
- require MLflow in default tests;
- run GEPA inside `/api/chat`;
- execute skill scripts on the FastAPI host;
- leak raw paths or provider errors;
- remove compatibility imports without tests;
- turn WebSocket removal into a backend phase;
- skip validation.

## Implementation order from now

```text
1. Phase 6A — Observability surface audit.
2. Phase 6B — Observability events/spans/recorder/redaction.
3. Phase 6C — Trace classifier and performance summary.
4. Phase 6D — Optional MLflow adapter and ingestion.
5. Phase 6E — Trace API/runtime integration.
6. Phase 7 — Config audit and typed config.
7. Phase 8 — GEPA and quality lane.
8. Phase 9 — Direct RLM default switch.
9. Phase 10 — Backend legacy cleanup and frontend SSE handoff readiness.
```

## Success statement

The backend refactor is done when Fleet can run direct RLM by default, produce safe streamed chat output over `/api/chat` SSE, preserve WebSocket compatibility where needed, record backend-owned trace/performance/MLflow data, use Daytona safely for tools/scripts/files/artifacts, and keep legacy runtime as a tested fallback.
