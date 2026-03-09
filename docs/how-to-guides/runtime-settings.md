# Runtime Settings API

The Runtime Settings API provides programmatic access to configure language models and Modal credentials for the Fleet-RLM runtime. Use these endpoints to inspect current settings, apply updates, and check runtime health.

## Overview

Runtime settings are stored in a project-local `.env` file and loaded at startup. The API provides:

- **Read access** to all runtime settings (secrets masked)
- **Write access** restricted to local development environments
- **Health checks** for Modal and LM connectivity

## Endpoints

### GET /api/v1/runtime/settings

Retrieve a snapshot of current runtime settings with secret values masked.

**Response Schema (`RuntimeSettingsSnapshot`):**

| Field | Type | Description |
|-------|------|-------------|
| `env_path` | string | Absolute path to the `.env` file being used |
| `keys` | string[] | List of setting keys returned |
| `values` | object | Key-value pairs with secrets masked (e.g., `sk-...yz`) |
| `masked_values` | object | Alias for `values` (backward compatibility) |

**Example Response:**

```json
{
  "env_path": "/path/to/project/.env",
  "keys": [
    "DSPY_LM_MODEL",
    "DSPY_DELEGATE_LM_MODEL",
    "DSPY_DELEGATE_LM_SMALL_MODEL",
    "DSPY_LLM_API_KEY",
    "DSPY_LM_API_BASE",
    "DSPY_LM_MAX_TOKENS",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "SECRET_NAME",
    "VOLUME_NAME"
  ],
  "values": {
    "DSPY_LM_MODEL": "openai/gpt-4o",
    "DSPY_DELEGATE_LM_MODEL": "openai/gpt-4o-mini",
    "DSPY_DELEGATE_LM_SMALL_MODEL": "",
    "DSPY_LLM_API_KEY": "sk-...yz",
    "DSPY_LM_API_BASE": "",
    "DSPY_LM_MAX_TOKENS": "",
    "MODAL_TOKEN_ID": "",
    "MODAL_TOKEN_SECRET": "***",
    "SECRET_NAME": "LITELLM",
    "VOLUME_NAME": "fleet-rlm-memory"
  },
  "masked_values": {
    "DSPY_LM_MODEL": "openai/gpt-4o",
    "DSPY_DELEGATE_LM_MODEL": "openai/gpt-4o-mini",
    "DSPY_DELEGATE_LM_SMALL_MODEL": "",
    "DSPY_LLM_API_KEY": "sk-...yz",
    "DSPY_LM_API_BASE": "",
    "DSPY_LM_MAX_TOKENS": "",
    "MODAL_TOKEN_ID": "",
    "MODAL_TOKEN_SECRET": "***",
    "SECRET_NAME": "LITELLM",
    "VOLUME_NAME": "fleet-rlm-memory"
  }
}
```

### PATCH /api/v1/runtime/settings

Update runtime settings in the `.env` file and apply changes to the running process.

**Constraints:**

- **Local-only**: This endpoint returns `403 Forbidden` unless `APP_ENV=local`
- **Allowlisted keys**: Only specific keys may be updated (see below)
- **Immediate effect**: Model changes (`DSPY_LM_MODEL`, `DSPY_DELEGATE_LM_MODEL`, `DSPY_DELEGATE_LM_SMALL_MODEL`) are applied to in-memory config immediately

**Request Schema (`RuntimeSettingsUpdateRequest`):**

| Field | Type | Description |
|-------|------|-------------|
| `updates` | object | Key-value pairs to update |

**Response Schema (`RuntimeSettingsUpdateResponse`):**

| Field | Type | Description |
|-------|------|-------------|
| `updated` | string[] | List of keys that were actually updated |
| `env_path` | string | Path to the `.env` file that was modified |

**Allowed Keys:**

| Key | Description |
|-----|-------------|
| `DSPY_LM_MODEL` | Planner/agent model (e.g., `openai/gpt-4o`) |
| `DSPY_DELEGATE_LM_MODEL` | Delegate model for sub-agent tasks |
| `DSPY_DELEGATE_LM_SMALL_MODEL` | Smaller delegate model for lightweight tasks |
| `DSPY_LLM_API_KEY` | API key for the LM provider |
| `DSPY_LM_API_BASE` | Custom API base URL (optional) |
| `DSPY_LM_MAX_TOKENS` | Maximum tokens for responses (optional) |
| `MODAL_TOKEN_ID` | Modal authentication token ID |
| `MODAL_TOKEN_SECRET` | Modal authentication token secret |
| `SECRET_NAME` | Modal secret name for runtime credentials |
| `VOLUME_NAME` | Modal volume name for persistence |

**Example Request:**

```bash
curl -X PATCH http://localhost:8000/api/v1/runtime/settings \
  -H "Content-Type: application/json" \
  -d '{"updates": {"DSPY_LM_MODEL": "openai/gpt-4o-mini"}}'
```

**Example Response:**

```json
{
  "updated": ["DSPY_LM_MODEL"],
  "env_path": "/path/to/project/.env"
}
```

**Error Responses:**

- `403 Forbidden`: `APP_ENV` is not `local`
- `400 Bad Request`: Invalid or non-allowlisted key in updates

### GET /api/v1/runtime/status

Retrieve comprehensive runtime status including active models, preflight checks, and connectivity test results.

**Response Schema (`RuntimeStatusResponse`):**

| Field | Type | Description |
|-------|------|-------------|
| `app_env` | string | Current application environment (`local`, `production`, etc.) |
| `write_enabled` | boolean | Whether `PATCH /settings` is allowed |
| `ready` | boolean | Overall runtime readiness (both Modal and LM tests passed) |
| `active_models` | object | Currently configured models (see below) |
| `llm` | object | LLM preflight check results |
| `modal` | object | Modal preflight check results |
| `tests` | object | Cached connectivity test results |
| `guidance` | string[] | List of remediation suggestions |

**Active Models Schema (`RuntimeActiveModels`):**

| Field | Type | Description |
|-------|------|-------------|
| `planner` | string | Active planner model from `DSPY_LM_MODEL` |
| `delegate` | string | Active delegate model from `DSPY_DELEGATE_LM_MODEL` |
| `delegate_small` | string | Active small delegate model from `DSPY_DELEGATE_LM_SMALL_MODEL` |

**Example Response:**

```json
{
  "app_env": "local",
  "write_enabled": true,
  "ready": true,
  "active_models": {
    "planner": "openai/gpt-4o",
    "delegate": "openai/gpt-4o-mini",
    "delegate_small": ""
  },
  "llm": {
    "model_set": true,
    "api_key_set": true,
    "planner_configured": true
  },
  "modal": {
    "credentials_from_env": false,
    "credentials_from_profile": true,
    "credentials_available": true,
    "secret_name_set": true,
    "secret_name": "LITELLM",
    "configured_volume": "fleet-rlm-memory"
  },
  "tests": {
    "modal": {
      "kind": "modal",
      "ok": true,
      "preflight_ok": true,
      "checked_at": "2025-01-15T10:30:00.000000+00:00",
      "checks": { "credentials_available": true, "secret_name_set": true },
      "guidance": [],
      "latency_ms": 1500,
      "output_preview": "ok"
    },
    "lm": {
      "kind": "lm",
      "ok": true,
      "preflight_ok": true,
      "checked_at": "2025-01-15T10:30:05.000000+00:00",
      "checks": { "model_set": true, "api_key_set": true },
      "guidance": [],
      "latency_ms": 850,
      "output_preview": "OK"
    }
  },
  "guidance": []
}
```

## Model Configuration

### DSPY_LM_MODEL

The primary language model used for the planner/agent. This model orchestrates the ReAct loop, selects tools, and coordinates sub-agent delegation.

**Format:** `provider/model-name` (e.g., `openai/gpt-4o`, `anthropic/claude-3-5-sonnet-20241022`)

**Default:** None (must be configured)

**Example:**

```bash
# In .env
DSPY_LM_MODEL=openai/gpt-4o
```

### DSPY_DELEGATE_LM_MODEL

The model used for delegated sub-agent tasks. Typically a smaller, faster model for focused operations.

**Format:** `provider/model-name`

**Default:** Inherits from `DSPY_LM_MODEL` if not set

**Example:**

```bash
# In .env
DSPY_DELEGATE_LM_MODEL=openai/gpt-4o-mini
```

### DSPY_DELEGATE_LM_SMALL_MODEL

An optional smaller delegate model for lightweight tasks. Useful for cost optimization on simple operations.

**Format:** `provider/model-name`

**Default:** Empty (falls back to `DSPY_DELEGATE_LM_MODEL`)

## Connectivity Tests

### POST /api/v1/runtime/tests/modal

Test Modal connectivity by creating a sandbox and running a simple Python command.

**Response Schema (`RuntimeConnectivityTestResponse`):**

| Field | Type | Description |
|-------|------|-------------|
| `kind` | string | `"modal"` |
| `ok` | boolean | Overall test success |
| `preflight_ok` | boolean | Whether preflight checks passed |
| `checked_at` | string | ISO timestamp of test execution |
| `checks` | object | Individual check results |
| `guidance` | string[] | Remediation suggestions |
| `latency_ms` | integer | Test duration in milliseconds |
| `output_preview` | string | Output from sandbox command |
| `error` | string | Error message if failed |

### POST /api/v1/runtime/tests/lm

Test LM connectivity by sending a simple prompt to the configured model.

**Response Schema:** Same as Modal test, with `kind: "lm"`

## Local-Only Writes

Runtime settings writes are intentionally restricted to local development:

- `PATCH /api/v1/runtime/settings` succeeds only when `APP_ENV=local`
- Non-local environments return `403 Forbidden` for runtime writes
- Read endpoints (`GET /settings`, `GET /status`) remain available in all environments
- Connectivity tests (`POST /tests/modal`, `POST /tests/lm`) work in all environments

This restriction prevents accidental credential changes in production and ensures settings changes go through proper deployment pipelines.

## Troubleshooting

### Model Not Resolving

If `active_models` shows empty strings:

1. Verify `DSPY_LM_MODEL` is set in your `.env` file
2. Check the file path matches `env_path` from `GET /settings`
3. Restart the server to reload environment variables

### Modal Connection Failing

If Modal tests fail:

1. Run `modal setup` to configure credentials via CLI profile
2. Or set `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in `.env`
3. Ensure `SECRET_NAME` points to an existing Modal secret
4. Verify the Modal secret contains required credentials

### LM Connection Failing

If LM tests fail:

1. Verify `DSPY_LM_MODEL` is set (e.g., `openai/gpt-4o`)
2. Verify `DSPY_LLM_API_KEY` contains a valid API key
3. For custom endpoints, set `DSPY_LM_API_BASE`
4. Check API quota and rate limits

### 403 on PATCH

If you receive `403 Forbidden` when updating settings:

1. Verify `APP_ENV=local` in your environment
2. The endpoint is intentionally disabled in production
3. Use deployment pipelines for production configuration changes
