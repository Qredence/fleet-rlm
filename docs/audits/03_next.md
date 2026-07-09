# Fleet RLM Refactor — What Will Be Done Next

## Next named phase

The next named phase is:

```text
Phase 6 — Trace, Transcript, Performance, and MLflow
```

## Phase 6 goal

Make backend spans and MLflow traces the canonical observability layer for Fleet.

Phase 6 should produce a unified observability foundation that both `legacy_agent_runtime` and `direct_rlm` can emit into, while preserving existing public contracts.

## Contracts to preserve

Phase 6 must preserve these contracts:

```text
SessionTraceDebugSpan
SessionTracePerformanceSummary
SessionTracePerformanceSpanSummary
SessionTraceItem
SessionTraceListResponse
TraceFeedbackRequest
RunStepItem
RuntimeEventKind.MLFLOW_SPAN
RuntimeEvent
POST /api/chat
WebSocket execution path
ActiveSkills
load_skill
Daytona volume/session state
```

## Target rendering map

The backend should own the mapping from runtime/trace events to UI rendering categories.

Target mapping:

```text
assistant_text -> AI SDK text parts
reasoning      -> reasoning parts
tool           -> tool parts + data-span
sandbox        -> data-span / data-sandbox
status_note    -> data-span / status
artifact       -> data-artifact
task           -> data-task
performance    -> data-performance
mlflow_span    -> data-span / trace debug panel
non_rendered   -> trace debug only
```

## MLflow responsibilities

MLflow should be a trace backend and quality/optimization integration point, not a required runtime dependency.

MLflow should be usable for:

- LLM span timing;
- tool span timing;
- RLM iteration spans;
- adapter fallback tracking;
- parse error tracking;
- token usage;
- performance summary source data;
- quality/evaluation run tracking;
- GEPA optimization tracking.

## MLflow boundaries

Phase 6 must enforce:

- MLflow disabled by default unless configured;
- no MLflow server required for default tests;
- no MLflow requirement for local dev;
- no raw provider errors leaked through MLflow/client surfaces;
- MLflow concerns stay out of transport projection logic.

## Target packages

Phase 6 may introduce or complete these packages:

```text
src/fleet_rlm/observability/
  __init__.py
  events.py
  recorder.py
  spans.py
  token_usage.py
  mlflow.py
  redaction.py

src/fleet_rlm/traces/
  classifier.py
  performance.py
  feedback.py
  mlflow_ingest.py
```

## Phase 6 implementation slices

### 6A — Observability surface audit

Create an audit of current trace/runtime/performance/MLflow surfaces.

Deliverable:

```text
docs/audits/phase6-observability-surface-audit.md
```

It should map:

- existing runtime events;
- existing trace schemas;
- existing performance summary schemas;
- existing API routes/services;
- existing MLflow span events;
- existing quality/GEPA references;
- gaps to close in Phase 6.

### 6B — Canonical observability event model

Create a canonical internal observability event/span model that can consume both legacy and direct RLM runtime events.

Expected modules:

```text
observability/events.py
observability/spans.py
observability/redaction.py
```

### 6C — Recorder foundation

Create a trace recorder seam that receives runtime events and produces backend trace/debug/performance records.

Expected module:

```text
observability/recorder.py
```

### 6D — Trace classifier and transcript mapping

Create a classifier that maps runtime/observability events into render categories and debug-only categories.

Expected module:

```text
traces/classifier.py
```

### 6E — Performance summary foundation

Create or harden performance summary generation across both runtime backends.

Expected module:

```text
traces/performance.py
```

### 6F — Optional MLflow adapter and ingest

Create an optional MLflow adapter and ingest path that does not require MLflow in default tests.

Expected modules:

```text
observability/mlflow.py
traces/mlflow_ingest.py
```

### 6G — API/service integration

Wire trace/debug/performance routes/services to the canonical recorder/classifier where appropriate, preserving API contracts.

## Phase 6 acceptance criteria

Phase 6 is complete when:

- legacy and direct RLM both emit traceable events;
- MLflow spans can be ingested into `SessionTraceDebugSpan`;
- performance summary works for both backends;
- non-rendered spans do not appear in the main transcript;
- client-facing errors are sanitized;
- detailed diagnostics remain server-side;
- default tests pass without MLflow server;
- trace/debug/performance APIs remain compatible;
- `POST /api/chat` and WebSocket execution paths remain usable by final validation.

## After Phase 6

After Phase 6, the planned order is:

1. Phase 7 — Config Audit and Typed Config.
2. Phase 8 — GEPA and Quality Lane.
3. Phase 9 — Direct RLM Default Switch.
4. Phase 10 — Frontend SSE Client and Legacy Cleanup.
