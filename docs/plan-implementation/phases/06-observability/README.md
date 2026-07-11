# Observability dossier

## Phase 6 — Trace, transcript, performance, and MLflow

- **Order:** `6`
- **Status:** `in_progress_uncommitted`
- **Track:** `Observability`
- **Summary:** Record both runtime backends through one provider-neutral observability seam.

### Current evidence state

Commit `29701f06` contains the recorder, redaction, classifier, performance,
trace-service, transport-context, schema, promotion-harness, and initial tests.
On 2026-07-11, the frontend teardown defect was repaired, the FastAPI SSE
generator lifecycle and MLflow trace correlation were verified, a live
direct-RLM `POST /api/chat` turn produced a correlated MLflow trace, and the
full repository/API gates passed. All Phase 6 acceptance criteria are complete
in the current working tree; the status remains `in_progress_uncommitted` until
these changes are committed. Phase 9 backend promotion remains separate and
gated.

### Goal and stable interfaces

One recorder observes the existing `RuntimeEvent` stream before SSE/WebSocket
projection. It must consume `RuntimeEventKind.MLFLOW_SPAN` rather than introduce a
second event vocabulary. Session trace-debug, performance, run-step, feedback,
and mapped-render schemas remain compatible.

MLflow is an optional adapter owned by observability/quality. It is disabled
unless configured, lazily imported, and never required by default tests or local
development. Raw operational failures stay in server logs; client projections
use recursively redacted events and stable error codes.

### Rendering classification

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

### Compatibility and safety

- Preserve `SessionTraceDebugSpan`, performance summaries, trace items,
  feedback requests, run steps, and existing mapped-render values.
- Preserve `POST /api/chat`, WebSocket controls, Skills, attachments, artifacts,
  Daytona state, and both transport projectors.
- Sanitize direct-runner errors, trace-feedback adapter errors, and trace-debug
  previews before they become client-visible.
- Keep quality/optimization execution outside chat.

### Non-goals

- Replace session storage schemas or remove public trace routes.
- Require a live relay to produce the direct-RLM post-turn record.
- Add automatic fallback/replay between runtime backends.
- Make MLflow a required runtime or test dependency.

### Acceptance criteria

- [x] Legacy and direct fixture streams pass through the same recorder interface.
- [x] MLflow-shaped spans ingest into the existing `SessionTraceDebugSpan` interface.
- [x] Disabled MLflow is a no-op and imports no MLflow runtime.
- [x] Client-facing errors, paths, and secret-shaped values are redacted.
- [x] Detailed operational diagnostics remain server-side.
- [x] Trace-debug classification remains additive and `non_rendered` stays debug-only.
- [x] Performance aggregation works from sanitized recorded spans.
- [x] Trace/debug/performance interfaces and both chat transports remain compatible in focused validation.
- [x] Live direct-RLM trace-span evidence is recorded through FastAPI SSE.
- [x] The full repository gate passes without unhandled frontend-test rejections.

### Evidence

- [MLflow and trace parity audit](evidence-mlflow-trace-parity.md)
- [Phase 6 observability surface and implementation evidence](evidence-observability-surface.md)
- [Task 1 validation record](evidence-task-1-validation.md)
- [Roadmap-claim audit, 2026-07-10](evidence-roadmap-claim-audit-2026-07-10.md)
- [Frontend test-teardown repair, 2026-07-11](evidence-frontend-test-teardown-2026-07-11.md)
- [Direct-RLM FastAPI SSE and MLflow evidence, 2026-07-11](evidence-direct-rlm-sse-mlflow-2026-07-11.md)

### Validation

```bash
uv run pytest tests/unit/observability/ tests/unit/api/test_trace_service.py tests/unit/api/test_session_trace_debug.py
make api-check
```

## Deferred gaps

- Live `TurnProgressRelay` behavior for direct RLM.
- Exact direct-vs-legacy cancellation and fallback/replay parity.
- Any session-storage schema replacement or public route removal.
- Phase 9's multi-run backend-promotion matrix and default-backend switch.
