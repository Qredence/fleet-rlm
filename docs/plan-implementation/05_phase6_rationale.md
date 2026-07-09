# Phase 6 Rationale — Trace, Transcript, Performance, and MLflow

## Why Phase 6 is next

Fleet has completed the main runtime capability substrate through Phase 5.

Now the system needs a canonical observability layer before direct RLM can become default.

Phase 6 is the bridge between:

```text
runtime capability
```

and:

```text
safe default direct RLM execution
quality optimization
frontend trace/debug rendering
```

## What Phase 6 must solve

Phase 6 must make these backend concepts consistent across legacy and direct RLM:

```text
trace spans
transcript mapping
runtime event classification
performance summaries
MLflow span ingestion
token usage where available
tool/sandbox timing
adapter fallback tracking
parse error tracking
redaction
```

## Why trace belongs in the backend

The backend has the source-of-truth information:

- which runtime backend ran;
- which skills were active;
- which tools were called;
- which sandbox operations occurred;
- which artifacts were produced;
- which attachment metadata was provided;
- which LLM calls happened;
- which errors were internal vs public;
- which spans should be rendered vs debug-only.

The frontend should not infer these from arbitrary stream fragments.

## Why transcript mapping matters

The user-facing transcript and the debug trace are not the same thing.

A single turn may contain:

```text
assistant text
reasoning
status messages
tool calls
tool results
sandbox execution logs
artifact refs
MLflow spans
performance metadata
non-rendered debug details
```

Phase 6 must classify these so that:

- the transcript shows user-relevant output;
- debug panels show internal detail;
- performance panels show timing/counts;
- frontend rendering is stable;
- sensitive internals stay server-side.

## Why MLflow is part of Phase 6

MLflow should not be an afterthought.

Fleet needs MLflow for:

- LLM span timing;
- tool span timing;
- RLM iteration spans;
- adapter fallback tracking;
- parse error tracking;
- token usage;
- performance summary source data;
- quality/evaluation run tracking;
- GEPA optimization tracking.

But MLflow must remain optional.

## MLflow boundaries

Phase 6 must enforce:

```text
MLflow disabled by default unless configured.
No MLflow server required for default tests.
No MLflow server required for local dev.
No MLflow credentials leaked to clients.
No raw provider errors leaked through MLflow surfaces.
No GEPA execution in normal /api/chat turns.
```

## Why Phase 6 should be staged

Phase 6 should not be one giant observability rewrite.

Recommended slices:

```text
6A Surface audit
6B Observability events/spans/recorder/redaction
6C Trace classifier and performance summaries
6D Optional MLflow adapter and ingestion
6E Trace API/runtime integration
```

## Final Phase 6 acceptance

Phase 6 is complete when:

- legacy and direct RLM both emit traceable events;
- trace classifier is backend-neutral;
- performance summary works for both backends;
- MLflow spans can be ingested when enabled;
- MLflow remains optional;
- non-rendered spans stay out of the main transcript;
- client-facing errors are sanitized;
- detailed diagnostics remain server-side;
- `/api/chat` and WebSocket paths remain compatible.
