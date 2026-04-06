# Runtime Settings

This guide covers the local runtime settings surfaces exposed under
`/api/v1/runtime/*`.

## Scope

- `GET /api/v1/runtime/settings` returns the current editable settings snapshot.
- `PATCH /api/v1/runtime/settings` updates local `.env` values when
  `APP_ENV=local`.
- `GET /api/v1/runtime/status` returns current readiness, active models, and
  cached connectivity test results.
- `POST /api/v1/runtime/tests/daytona` and `POST /api/v1/runtime/tests/lm`
  run live connectivity checks.

## Editable Keys

The runtime settings allowlist is currently:

- `DSPY_LM_MODEL`
- `DSPY_DELEGATE_LM_MODEL`
- `DSPY_DELEGATE_LM_SMALL_MODEL`
- `DSPY_DELEGATE_LM_MAX_TOKENS`
- `DSPY_LLM_API_KEY`
- `DSPY_LM_API_BASE`
- `DSPY_LM_MAX_TOKENS`
- `DAYTONA_API_KEY`
- `DAYTONA_API_URL`
- `DAYTONA_TARGET`

Legacy sandbox-provider and old credential env vars are no longer part of the
editable runtime surface.

## Example Settings Snapshot

```json
{
  "env_path": "/path/to/.env",
  "keys": [
    "DSPY_LM_MODEL",
    "DSPY_DELEGATE_LM_MODEL",
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL",
    "DAYTONA_TARGET"
  ],
  "values": {
    "DSPY_LM_MODEL": "openai/gpt-4o",
    "DSPY_DELEGATE_LM_MODEL": "openai/gpt-4o-mini",
    "DAYTONA_API_KEY": "***",
    "DAYTONA_API_URL": "https://app.daytona.io/api",
    "DAYTONA_TARGET": "default"
  }
}
```

## Example Local Update

```bash
curl -X PATCH http://localhost:8000/api/v1/runtime/settings \
  -H "Content-Type: application/json" \
  -d '{
    "updates": {
      "DSPY_LM_MODEL": "openai/gpt-4o-mini",
      "DAYTONA_API_URL": "https://app.daytona.io/api"
    }
  }'
```

## Runtime Status

`GET /api/v1/runtime/status` reports:

- `ready`
- `sandbox_provider`
- `active_models`
- `llm`
- `daytona`
- `mlflow`
- `tests`
- `guidance`

The runtime provider is Daytona-only on the public surface. Legacy
`SANDBOX_PROVIDER` values in `.env` are ignored during startup rather than
treated as errors.

## Connectivity Tests

### `POST /api/v1/runtime/tests/daytona`

Checks Daytona configuration and API connectivity using the current
`DAYTONA_API_KEY`, `DAYTONA_API_URL`, and optional `DAYTONA_TARGET`.

### `POST /api/v1/runtime/tests/lm`

Checks LM configuration using the current planner model and key settings.

## Troubleshooting

### Daytona test failing

Check:

- `DAYTONA_API_KEY`
- `DAYTONA_API_URL`
- optional `DAYTONA_TARGET`

The canonical smoke command is:

```bash
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

### LM test failing

Check:

- `DSPY_LM_MODEL`
- `DSPY_LLM_API_KEY` or `DSPY_LM_API_KEY`
- optional `DSPY_LM_API_BASE`

### Settings write rejected

`PATCH /api/v1/runtime/settings` is local-only. Non-local environments return
`403 Forbidden`.
