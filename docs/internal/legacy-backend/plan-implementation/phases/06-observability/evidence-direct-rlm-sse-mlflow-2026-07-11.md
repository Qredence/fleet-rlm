# Direct-RLM FastAPI SSE and MLflow evidence — 2026-07-11

## Scope

This record closes Phase 6's live direct-RLM observability criterion through
the intended transport: `POST /api/chat` served by FastAPI
`EventSourceResponse`. It does not exercise or promote WebSocket execution.
The multi-run backend promotion matrix remains owned by Phase 9.

## Lifecycle findings and repair

Focused FastAPI SSE regression coverage exposed two connected issues:

1. the application root MLflow span's concrete trace ID was not cached when
   the span opened, so the direct-RLM completion `MLFLOW_SPAN` could lack its
   MLflow correlation; and
2. `project_sse()` returned after emitting a terminal frame without explicitly
   closing its upstream async iterators, allowing request-context cleanup to
   occur later from a different async context.

The repair caches the root span trace ID in the request context and makes the
SSE projector/preamble/runtime wrappers close their upstream iterators in
`finally` blocks. The canonical FastAPI generator now owns cleanup on normal
completion, error, cancellation, and client disconnect.

Deterministic tests prove that:

- a terminal SSE projection closes the upstream event stream immediately;
- `POST /api/chat` projects a direct-RLM completion as `data-span` with the
  expected trace ID; and
- opening the MLflow application span populates result correlation metadata.

Focused command and result:

```bash
uv run pytest -q \
  tests/unit/events/test_project_sse.py \
  tests/unit/api/test_chat_sse.py \
  tests/unit/integrations/test_mlflow_context.py
```

Result: `150` passed, `1` skipped, with no unhandled async errors.

## Live FastAPI SSE run

The API ran locally with `EXECUTION_BACKEND=direct_rlm`, MLflow enabled at
`http://127.0.0.1:5001`, Daytona configured from the local environment, and the
working provider model `nemotron-3-ultra-free`. Authentication was disabled for
the bounded local request only.

```bash
curl --fail-with-body --no-buffer --max-time 180 \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"messages":[{"role":"user","content":"Reply with exactly: PHASE6_SSE_OK"}],"session_id":"phase6-sse-live-20260711","execution_mode":"rlm_only","trace":true}' \
  http://127.0.0.1:8001/api/chat
```

Observed terminal evidence:

```text
data: {"type":"text-delta","delta":"PHASE6_SSE_OK"}
data: {"type":"data-span","name":"direct_rlm.turn","status":"completed","trace_id":"tr-e8c5f891d92ec7fe2de91df05f6763ae","duration_ms":28703}
data: {"type":"finish"}
data: [DONE]
```

The same trace was retrieved from MLflow after the request:

```text
trace_id=tr-e8c5f891d92ec7fe2de91df05f6763ae
state=OK
execution_duration_ms=28139
span_count=14
root_status=OK
```

Recorded span names included `fleet_rlm.chat_turn`, `_StreamingRLM.forward`,
`fleet_rlm.rlm_action_generation`, `fleet_rlm.rlm_repl_execute`,
`repl_execute`, `LM.__call__`, and DSPy adapter/predict spans. The API server
was stopped after the request and no Phase 6 validation sandbox remained.

## Repository gate

```bash
make quality-gate
make api-check
```

Both commands passed. The quality gate included backend unit/contracts and
integration suites, release metadata, documentation/harness checks, duplicate
and import-boundary scans, frontend type/lint/API checks, `430` passing
frontend unit tests (`15` skipped), and client/SSR production builds.

## Boundary decision

This evidence completes Phase 6 observability without changing the default
backend. `legacy_agent_runtime` remains the default, and Phase 9 remains
`promotion_gated` until its separate backend-promotion matrix passes.
