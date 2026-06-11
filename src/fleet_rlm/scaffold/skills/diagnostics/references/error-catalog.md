# Error Catalog

Known error messages, their root causes, and fixes.

---

## "Broker server failed to start within timeout"

**Cause:** The Daytona bridge broker process did not become healthy within the allotted time. Usually happens on cold starts or resource-constrained environments.

**Fix:**
- Increase `DAYTONA_BROKER_HEALTH_TIMEOUT` (default: 180s)
- Check system resources (CPU/memory pressure can delay startup)
- Verify the broker port (3000) is not already in use: `lsof -i :3000`

---

## "budget_exhausted"

**Cause:** The parent `max_llm_calls` budget has been fully consumed. This happens when delegation fan-out is too wide or child tasks are not bounded.

**Fix:**
- Reduce delegation fan-out (fewer concurrent sub_rlm calls)
- Increase `max_llm_calls` in the execution request
- Add budget guards in delegation: allocate fixed fractions to children
- Check for recursive delegation (depth > 1 causes exponential consumption)

---

## "DaytonaInterpreter: sandbox creation failed"

**Cause:** Unable to create a new sandbox instance. Common reasons: invalid API credentials, quota exceeded, or API endpoint unreachable.

**Fix:**
- Verify `DAYTONA_API_KEY` is set and valid (not expired)
- Verify `DAYTONA_API_URL` points to the correct endpoint
- Check runtime diagnostics: `GET /api/v1/runtime/status`
- Check active sandboxes: `GET /api/v1/sandboxes`
- If quota exceeded, wait for existing sandboxes to terminate or request increase

---

## "Connection refused on port 3000"

**Cause:** The bridge broker is not running. The sandbox may have crashed, or the broker process was never started.

**Fix:**
- Check if the broker process is alive: `ps aux | grep broker`
- Restart the sandbox: the broker auto-starts with the sandbox
- Check sandbox logs for crash indicators
- If persistent, recreate the sandbox (may have corrupted state)

---

## "Turn never escalates to tools or RLM"

**Cause:** The typed `RouteTurnSignature` router keeps classifying turns as `direct`, so the escalating runtime never engages the ReAct tool loop or the RLM sandbox.

**Fix:**
- Inspect the `routing_decision` payload on the turn (`tools_react`, `router_rlm`, deterministic `*_rlm` routes)
- Force RLM mode explicitly: set `execution_mode="rlm"` in the request
- Confirm tools are registered — with an empty tool list the router downgrades `tools` to `direct`
- If the router LM is failing, the module logs "Turn routing failed" and degrades to `direct`; check planner LM configuration

---

## "Volume mount failed"

**Cause:** The specified volume could not be mounted to the sandbox. Typically a name conflict (volume already attached elsewhere) or quota exceeded.

**Fix:**
- Try a different `volume_name` to avoid conflicts
- Check active sandboxes: `GET /api/v1/sandboxes`
- Inspect mounted volume contents: `GET /api/v1/runtime/volume/tree`
- Detach the volume from other sandboxes before re-mounting
- If quota exceeded, delete unused volumes first

---

## "Session restore failed"

**Cause:** The session manifest file could not be found or parsed. Usually happens after a schema migration that changes the session storage path.

**Fix:**
- Check that `sessions/<id>/conversation.json` exists at the expected path
- Verify the session ID is valid (not corrupted or truncated)
- If path changed due to migration, update the manifest lookup logic
- For unrecoverable sessions, start a new session (old context is lost)

---

## "MLflow tracking failed"

**Cause:** The MLflow tracking server is unreachable at the configured `MLFLOW_TRACKING_URI`.

**Fix:**
- Verify the server is running: `curl <MLFLOW_TRACKING_URI>/health`
- Start the server: `make mlflow-server`
- Check the URI is correct (default: `http://127.0.0.1:5001`)
- If running in Docker, ensure port mapping is correct
- Set `MLFLOW_ENABLED=false` to disable tracking temporarily (not recommended for production)

---

## Quick Reference

| Error pattern | First check | Likely fix |
|---------------|-------------|------------|
| `timeout` / `within timeout` | Resource pressure, port conflicts | Increase timeout, free ports |
| `budget` / `exhausted` | Delegation depth, fan-out | Reduce concurrency, increase budget |
| `creation failed` / `sandbox` | API credentials, quota | Verify keys, check quota |
| `connection refused` | Process not running | Restart service, check ports |
| `not detected` | Prompt/model mismatch | Force execution mode, check prompt |
| `mount failed` / `volume` | Name conflict, quota | Rename volume, clean up old volumes |
| `restore failed` / `session` | Path migration | Check manifest path |
| `tracking failed` / `MLflow` | Server unreachable | Start server, verify URI |
