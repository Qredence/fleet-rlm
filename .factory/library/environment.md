# Environment

Environment variables, external dependencies, and setup notes for the DSPy refactor mission.

**What belongs here:** required env vars, external services, provider notes, local setup caveats.
**What does NOT belong here:** service commands/ports (see `.factory/services.yaml`).

---

## Required Runtime Inputs

### DSPy / LM
- `DSPY_LM_MODEL` or equivalent configured planner model
- `DSPY_LM_API_KEY` or provider-specific API key consumed by the runtime settings flow
- Optional `LM_API_BASE` / proxy URL when routed through LiteLLM-compatible infrastructure

### Daytona
- `DAYTONA_API_KEY`
- `DAYTONA_API_URL`
- `DAYTONA_TARGET`
- Any volume/workspace defaults used by the local runtime configuration

### MLflow / optimization
- Existing MLflow server is available on `http://127.0.0.1:5001`
- Prefer `MLFLOW_AUTO_START=false` during local mission validation so workers reuse the existing service instead of starting a competing one
- Optimization requires MLflow to be enabled plus a valid trace dataset and resolvable DSPy program spec

## Local Notes

- The local app entrypoint for validation is `uv run fleet-rlm serve-api --host 127.0.0.1 --port 8000`.
- The packaged UI is served by FastAPI; a separate frontend dev server is not required for browser validation.
- During planning, live workspace execution failed because Daytona volume `rlm-volume-dspy` was stuck in `pending_create`. This mission explicitly includes fixing that readiness path.

## Safety Notes

- Do not commit secrets written through runtime settings.
- Treat runtime settings secret fields as write-only; preserve redaction behavior.
- Reuse the configured MLflow service rather than mutating its deployment.
