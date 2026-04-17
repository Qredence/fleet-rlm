# MLflow Tracing, Feedback, Eval, and Optimization

Use this guide when you want `fleet-rlm` to emit MLflow traces during live DSPy execution, collect human feedback on those traces, and reuse the annotated data for offline evaluation or DSPy optimization.

## 1. Start a Local MLflow Server

```bash
# from repo root
make mlflow-server
```

That starts an OSS MLflow tracking server on `http://127.0.0.1:5001` using `sqlite:///.data/mlruns.db` by default.

If you need a different local backend store, override `MLFLOW_LOCAL_BACKEND_STORE_URI` before running `make mlflow-server` or starting `uv run fleet web`.

### Optional: Auto-start MLflow in local development

If you prefer not to keep a second terminal open for `make mlflow-server`, the API
server now auto-starts MLflow by default in local development whenever
`MLFLOW_ENABLED=true` and `MLFLOW_TRACKING_URI` points at localhost. You can
still force the behavior explicitly:

```bash
# from repo root
export MLFLOW_AUTO_START=true
uv run fleet web
```

Notes:

- Auto-start only runs when `APP_ENV=local`.
- The port comes from `MLFLOW_TRACKING_URI`, so keep that value aligned with your
  local MLflow server.
- The backend store comes from `MLFLOW_LOCAL_BACKEND_STORE_URI` and defaults to
  `sqlite:///.data/mlruns.db`.
- Set `MLFLOW_AUTO_START=false` if you prefer to manage MLflow manually with
  `make mlflow-server`.
- Startup still succeeds if MLflow cannot be reached; the app reports MLflow as
  degraded instead of failing boot.

If the local startup logs report `Detected out-of-date database schema`, upgrade the configured backend store before retrying:

```bash
uv run mlflow db upgrade sqlite:///.data/mlruns.db
```

If the local tracking history does not matter, you can also delete the SQLite database file backing `MLFLOW_LOCAL_BACKEND_STORE_URI` and let MLflow recreate it on the next startup.

## 2. Enable MLflow in fleet-rlm

Set the following environment variables before starting the server or running offline scripts:

```bash
# from repo root
export MLFLOW_ENABLED=true
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
export MLFLOW_EXPERIMENT=fleet-rlm

# optional override for the local auto-started OSS server / make mlflow-server
# export MLFLOW_LOCAL_BACKEND_STORE_URI=sqlite:///.data/mlruns.db

# optional model registry / deployment id
# export MLFLOW_ACTIVE_MODEL_ID=model-123

# optional DSPy autolog controls
export MLFLOW_DSPY_LOG_TRACES_FROM_COMPILE=false
export MLFLOW_DSPY_LOG_TRACES_FROM_EVAL=true
export MLFLOW_DSPY_LOG_COMPILES=false
export MLFLOW_DSPY_LOG_EVALS=false
```

For protected remote MLflow servers, configure one of MLflow's native auth mechanisms before starting the app or running export/eval scripts:

```bash
# bearer-token auth
export MLFLOW_TRACKING_TOKEN=replace-with-token

# or basic auth
export MLFLOW_TRACKING_USERNAME=replace-with-username
export MLFLOW_TRACKING_PASSWORD=replace-with-password

# optional only for development against self-signed certs
# export MLFLOW_TRACKING_INSECURE_TLS=true
```

If you do not want MLflow for a given environment, set `MLFLOW_ENABLED=false` and startup will skip MLflow initialization entirely.

When MLflow is enabled, its initialization runs as an optional background startup
task. If startup cannot reach the tracking server, check `/api/v1/runtime/status`
for the `mlflow.startup_status` and `mlflow.startup_error` fields while you verify
connectivity, permissions, or auth settings.

When MLflow is enabled:

- WebSocket chat turns generate one `mlflow_client_request_id` per turn.
- Non-chat runner entry points generate one `mlflow_client_request_id` per invocation.
- Final/result payloads include `mlflow_trace_id` and `mlflow_client_request_id` when a trace was captured.
- The frontend workbench runtime preserves those identifiers from final websocket payloads so feedback/debugging flows can reuse them later.
- The trace metadata includes `mlflow.trace.session`, `mlflow.trace.user`, and `app_env`, plus any configured `MLFLOW_ACTIVE_MODEL_ID`.

## 3. Run the App

```bash
# from repo root
uv run fleet web
```

As you use the app, MLflow traces are recorded in the configured experiment.

## 4. Record Human Feedback and Ground Truth

Use the API-only feedback endpoint to mark a response correct/incorrect and optionally attach an expected response.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/traces/feedback \
  -H 'Content-Type: application/json' \
  -H 'X-Debug-Tenant-Id: default' \
  -H 'X-Debug-User-Id: analyst' \
  -d '{
    "client_request_id": "chat-abc123",
    "is_correct": false,
    "comment": "The answer skipped the root cause.",
    "expected_response": "The root cause was an expired Daytona credential."
  }'
```

The endpoint accepts either:

- `trace_id`
- `client_request_id`

At least one is required.

For live websocket-driven runs, use the `mlflow_trace_id` or `mlflow_client_request_id` returned in the final payload when available.

## 5. Export Annotated Traces

```bash
# from repo root
uv run python scripts/mlflow_cli.py export \
  --output artifacts/mlflow/annotated-traces.json
```

The exported rows use this shape:

```json
{
  "trace_id": "tr-...",
  "client_request_id": "chat-...",
  "inputs": { "question": "What happened?" },
  "outputs": "The deployment failed because ...",
  "expectations": { "expected_response": "The deployment failed because ..." },
  "feedback": {
    "response_is_correct": {
      "value": false,
      "rationale": "Missing the root cause",
      "source_id": "analyst"
    }
  }
}
```

## 6. Evaluate Annotated Traces

```bash
# from repo root
uv run python scripts/mlflow_cli.py evaluate \
  --input artifacts/mlflow/annotated-traces.json \
  --results-output artifacts/mlflow/evaluation-results.json
```

Default behavior:

- Filters to rows with `expectations.expected_response`
- Runs `mlflow.genai.evaluate(...)`
- Uses MLflow's `Correctness()` scorer first

Optional add-ons:

```bash
uv run python scripts/mlflow_cli.py evaluate \
  --include-safety \
  --guideline "The answer must cite the concrete failing component." \
  --guideline "The answer must stay concise."
```

## 7. Run GEPA from the Frontend Optimization Surface

For this repo, **GEPA is the canonical user-facing optimizer**. The main end-to-end
workflow lives in the web app under **Optimization**:

1. Open the **Optimization** surface.
2. Start from **Modules** if you already know the module you want to optimize, or
   start from **Datasets** if you need to upload JSON/JSONL data or export a
   session/transcript into a dataset first.
3. Use **Create** to review the GEPA run configuration:
   - selected module / resolved `program_spec`
   - uploaded or exported dataset
   - optimization intensity (`auto`)
   - train ratio
   - optional output path
4. Submit the run and monitor it in **Runs**.
5. Use **Compare** to inspect score deltas, prompt diffs, and per-example changes
   across completed GEPA runs.

Under the hood, GEPA runs keep MLflow DSPy compile/eval autologging enabled and
record consistent run metadata so the MLflow experiment reflects the same
workflow the frontend exposes.

## 8. Use the Offline MIPROv2 Helper

Exported trace datasets can also drive offline DSPy optimization.

```bash
# from repo root
uv run python scripts/mlflow_cli.py optimize \
  --dataset artifacts/mlflow/annotated-traces.json \
  --program your_package.your_module:build_program \
  --input-key question \
  --output-key answer \
  --output artifacts/mlflow/optimized-program.json
```

Notes:

- The optimizer defaults to `dspy.teleprompt.MIPROv2`.
- The script turns on MLflow DSPy compile/eval autologging for the optimization run.
- The dataset is split into train/validation partitions with `--train-ratio` (default `0.8`).
- The provided program symbol can be:
  - a `dspy.Module` instance
  - a `dspy.Module` subclass
  - a callable that returns a `dspy.Module`

This remains a secondary offline helper. The repo's primary product workflow is
the GEPA path exposed in the frontend and `/api/v1/optimization/*`.

## 9. Use the MLflow MCP Server

Install and run the MLflow MCP server with `uv`:

```bash
uv run --with "mlflow[mcp]>=3.10.0" mlflow mcp run
```

If your MCP client supports per-server env vars, point it at the same tracking server:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
uv run --with "mlflow[mcp]>=3.10.0" mlflow mcp run
```

Document the client-specific config in your local editor setup, but do not commit `.cursor/mcp.json`, `.vscode/mcp.json`, or other editor-local MCP files into this repository.
