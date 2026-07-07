---
name: diagnostics
description: "Diagnose fleet-rlm runtime failures, API contract drift, sandbox errors, and observability issues. Use when something is broken — Daytona connection failures, websocket mismatches, escalation not triggering, budget exhaustion, or missing traces."
---

# Diagnostics

## Symptom-to-Cause Decision Tree

1. **"Sandbox won't start"**
   - Check `DAYTONA_API_KEY` and `DAYTONA_API_URL` are set and non-empty
   - Run `daytona-smoke` to verify connectivity
   - Verify API key has not expired (keys rotate every 90 days)
   - Check runtime diagnostics: `GET /api/v1/runtime/status`
   - Check managed sandboxes: `GET /api/v1/sandboxes`

2. **"No response from websocket"**
   - Likely contract drift between frontend and backend
   - Check canonical fields: `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`
   - Verify `/api/v1/ws/execution` endpoint is registered and not shadowed
   - See `references/contract-surfaces.md` for full field list

3. **"Escalation not triggering"**
   - Inspect the `routing_decision` payload (`tools_react`, `router_rlm`, `url_document_rlm`, ...) emitted on the turn
   - Verify `execution_mode` is not overridden to `"direct"` in request
   - Confirm `FLEET_RLM_USE_ESCALATING_RUNTIME=true` in environment
   - Check that the escalation module is registered: `uv run fleet-rlm optimize list`

4. **"Budget exhausted too quickly"**
   - Trace `max_llm_calls` subdivision across child tasks
   - Check child lease allocation via `_remaining_llm_budget()`
   - Reduce delegation fan-out (fewer concurrent sub_rlm calls)
   - Look for recursive delegation patterns (delegation depth > 1)

5. **"Volume data missing after restart"**
   - Confirm `volume_name` is set on the interpreter configuration
   - Data in `/home/daytona/memory/` persists across restarts
   - Workspace files (`/home/daytona/workspace/`) are transient — wiped on restart
   - Check mounted volume contents: `GET /api/v1/runtime/volume/tree`
   - Check a specific volume file: `GET /api/v1/runtime/volume/file`

6. **"UI showing stale data"**
   - OpenAPI drift between backend routes and generated frontend client
   - Run `make api-check` to detect schema mismatches
   - Regenerate with `make api-sync`
   - See `references/contract-surfaces.md` for sync commands

7. **"No MLflow traces appearing"**
   - Verify `MLFLOW_ENABLED=true` in environment
   - Check `MLFLOW_TRACKING_URI` is reachable: `curl <uri>/health`
   - Confirm `initialize_mlflow()` is called at application startup
   - Check experiment exists: `curl <uri>/api/2.0/mlflow/experiments/get-by-name?experiment_name=fleet-rlm`

8. **"Tests failing unexpectedly"**
   - See Test Lane Selector below to pick the right test subset
   - Check if failure is environment-specific (missing env vars, port conflicts)
   - Run diagnostics script first: `uv run python src/fleet_rlm/scaffold/skills/diagnostics/scripts/diagnose.py`

---

## Risky Backend Files

Files most likely to cause breakage when modified — review changes carefully:

| File | Responsibility |
|------|---------------|
| `api/routers/ws/endpoint.py` | WebSocket `/api/v1/ws/execution` route registration |
| `api/routers/ws/connection_loop.py` | WebSocket receive/send coordination and command loop |
| `api/routers/ws/stream_loop.py` | Runtime stream fan-out for one chat turn |
| `api/routers/ws/stream_events.py` | Chat stream event projection to websocket messages |
| `api/routers/ws/stream_summary.py` | Runtime stream summary and final payload assembly |
| `api/routers/ws/turn_runner.py` | Per-turn runtime execution and cancellation handling |
| `api/routers/ws/turn_setup.py` | Request normalization, context paths, and auth/session setup |
| `api/routers/ws/commands.py` | Command dispatch (run, stop, history) |
| `api/runtime_services/settings.py` | Settings routes (read/write config) |
| `api/runtime_services/diagnostics.py` | Status and diagnostics endpoints |
| `runtime/execution/streaming_events.py` | Streaming event construction and serialization |

---

## Test Lane Selector

| Change type | Command | What it validates |
|-------------|---------|-------------------|
| Fast confidence | `make test-fast` | Unit + contracts parallel, integration serial |
| Quality gate | `make quality-gate` | Lint + format + types + tests + docs + frontend |
| Runtime/WS focus | `uv run pytest -q tests/unit/api/test_auth.py tests/unit/api/test_chat_persistence.py tests/unit/api/test_events.py tests/unit/api/test_ws_session_restore.py tests/unit/api/test_ws_turn_setup.py tests/unit/runtime/test_escalating_module.py` | WebSocket session lifecycle, auth, event streaming |
| Daytona focus | `uv run pytest -q tests/unit/integrations/daytona tests/unit/integrations/test_daytona_runtime.py tests/unit/integrations/test_daytona_sandbox_executor.py tests/unit/integrations/test_memory_db.py tests/unit/integrations/test_volume_seed_skills.py tests/unit/runtime/test_volume_memory_tools.py tests/unit/runtime/test_phase3_tools.py` | Sandbox creation, volumes, memory persistence |
| MLflow/observability | `uv run pytest -q tests/unit/quality/test_module_registry.py tests/unit/quality/test_optimization_runner.py tests/unit/quality/test_workspace_metrics.py tests/unit/api/test_bootstrap_observability.py tests/unit/integrations/test_observability.py` | Tracing, metrics logging, experiment tracking |

---

## Environment Diagnostics

Run the full diagnostic check:

```bash
uv run python src/fleet_rlm/scaffold/skills/diagnostics/scripts/diagnose.py
```

This script checks:
- Required environment variables are set
- Daytona API connectivity
- MLflow server reachability
- Port availability (3000, 5001, 8000)
- Volume mount status
- OpenAPI spec consistency

---

## See Also

- **sandbox-execution** — interpreter configuration validation, sandbox lifecycle
- **optimization** — when optimization runs fail (scorer errors, dataset issues, MLflow connection)
