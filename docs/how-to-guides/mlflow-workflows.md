# MLflow Tracing, Feedback, Eval, and Optimization

Use this guide when you want `fleet-rlm` to emit MLflow traces during live DSPy execution, collect human feedback on those traces, and reuse the annotated data for offline evaluation or DSPy optimization.

## 1. Start a Local MLflow Server

```bash
# from repo root
make mlflow-server
```

That starts an OSS MLflow tracking server on `http://127.0.0.1:5000` using `sqlite:///mlruns.db`.

## 2. Enable MLflow in fleet-rlm

Set the following environment variables before starting the server or running offline scripts:

```bash
# from repo root
export MLFLOW_ENABLED=true
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT=fleet-rlm

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

When MLflow is enabled:

- WebSocket chat turns generate one `mlflow_client_request_id` per turn.
- Non-chat runner entry points generate one `mlflow_client_request_id` per invocation.
- Final/result payloads include `mlflow_trace_id` and `mlflow_client_request_id` when a trace was captured.
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
    "expected_response": "The root cause was an expired Modal secret."
  }'
```

The endpoint accepts either:

- `trace_id`
- `client_request_id`

At least one is required.

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

## 7. Optimize a DSPy Program with MIPROv2

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

## 8. Use the MLflow MCP Server

Install and run the MLflow MCP server with `uv`:

```bash
uv run --with "mlflow[mcp]>=3.10.0" mlflow mcp run
```

If your MCP client supports per-server env vars, point it at the same tracking server:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 \
uv run --with "mlflow[mcp]>=3.10.0" mlflow mcp run
```

Document the client-specific config in your local editor setup, but do not commit `.cursor/mcp.json`, `.vscode/mcp.json`, or other editor-local MCP files into this repository.
