# Phase 6 Task 1 validation record

**Date:** 2026-07-10
**Baseline:** `bdd38f8978ac6bc775a844641d8bc784ecffd822`
**State:** implemented and validated in the current working tree; uncommitted.

## Scope validated

The Task 1 working tree adds the provider-neutral observability recorder,
recursive redaction, spans, token usage, optional MLflow adapter, shared runtime
recording, direct-RLM completion span, trace-debug classification/performance,
and sanitized trace-feedback/runtime errors. It preserves the canonical
`RuntimeEvent` vocabulary and existing routes/schemas.

The source checkout under `.codex/phase6-observability` and the salvage
worktree were read-only. Typed configuration, GEPA, default-backend promotion,
automatic legacy replay, and schema replacement were excluded.

## Behavior evidence

- Both runtime backends pass through one redacting recorder before transport
  projection.
- Direct RLM emits a safe completion `MLFLOW_SPAN` immediately before its
  terminal event without replaying through the legacy runtime.
- Disabled MLflow lifecycle functions are no-ops and do not import or initialize
  MLflow.
- Trace-debug adds artifact, task, performance, and MLflow-span render kinds
  while keeping `non_rendered` debug-only.
- Feedback and runtime failures use stable client-safe messages while detailed
  diagnostics remain server-side.

## Focused validation

```bash
uv run pytest -q tests/unit/observability \
  tests/unit/api/test_trace_service.py \
  tests/unit/api/test_session_trace_debug.py \
  tests/unit/api/test_ws_turn_runner.py \
  tests/unit/api/test_ws_turn_setup.py \
  tests/unit/rlm/test_direct_rlm_runner.py \
  tests/unit/runtime/agent/test_runtime_streaming_live.py \
  tests/unit/runtime_services/test_stream_turn_execution_backend.py \
  tests/unit/integrations/test_mlflow_context.py
```

Result: `103 passed`; one unrelated Starlette/httpx deprecation warning.

```bash
make api-sync && make api-check
```

Result: passed; the root and frontend OpenAPI artifacts were synchronized.

```bash
make format-check
git diff --check
```

Result: passed; 599 files were checked and no diff whitespace error was found.

## Follow-up safety validation

The WebSocket stream-error path was changed to reuse the canonical runtime-event
sanitizer instead of returning `str(exc)`. The red test exposed an API key and
absolute Daytona path in the client frame; the green validation was:

```bash
uv run pytest tests/unit/api/test_ws_turn_runner.py \
  tests/unit/observability/test_redaction.py -q
```

Result: `8 passed`; targeted Ruff, format, and whitespace checks passed.

Markerless attachment resolution was also hardened so the authenticated tenant,
user, and workspace must resolve the exact persisted external session before a
`PersistedSessionOwnerProof` is accepted. The red test demonstrated cross-owner
attachment metadata access; the green validation was:

```bash
uv run pytest tests/unit/files/test_attachment_resolution.py \
  tests/unit/api/test_ws_promotion_contract.py \
  tests/unit/api/test_ws_session_restore.py -q
```

Result: `25 passed`; targeted Ruff, format, type, and whitespace checks passed.

## Remaining gate

This record does not close Phase 6 or authorize Phase 9. The final repository
gate and configured live promotion matrix remain outstanding, and no commit is
recorded for the current Phase 6 implementation.
