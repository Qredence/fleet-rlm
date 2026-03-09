# Configuring Modal

`fleet-rlm` executes Python code in isolated Modal sandboxes. This guide covers authentication, volume persistence, timeout configuration, and environment variables.

## Overview

The `ModalInterpreter` class (`src/fleet_rlm/core/interpreter.py`) manages sandbox lifecycle:

- **Sandbox Creation**: Creates isolated Modal Sandbox instances with configurable images
- **Code Execution**: Runs Python code via a JSON protocol over stdin/stdout
- **Volume Persistence**: Supports persistent file storage across sessions
- **Secret Management**: Injects Modal Secrets for API keys and credentials

## Authentication

### Method 1: Interactive Setup (Recommended)

```bash
uv run modal setup
```

This stores credentials in `~/.modal.toml` with profile-based configuration.

### Method 2: Environment Variables

Set these environment variables for CI/CD or non-interactive environments:

```bash
export MODAL_TOKEN_ID="ak-..."
export MODAL_TOKEN_SECRET="as-..."
```

The system checks for these variables in the following priority:

1. Environment variables (`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`)
2. `~/.modal.toml` config file (loaded automatically)

See [Environment Variables Reference](#environment-variables-reference) for all available settings.

## Volume Configuration

Volumes provide persistent storage for sandbox-executed code. Files written to the volume mount path survive across interpreter sessions.

### Create a Volume

```bash
uv run modal volume create rlm-volume-dspy
```

### Volume Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `volume_name` | `None` | Modal Volume name for persistent storage |
| `volume_mount_path` | `/data` | Mount path inside sandbox |

### Usage

**Terminal chat with volume:**

```bash
uv run fleet --volume-name rlm-volume-dspy
```

**Programmatic usage:**

```python
from fleet_rlm import ModalInterpreter

interpreter = ModalInterpreter(
    volume_name="rlm-volume-dspy",
    volume_mount_path="/data"
)
interpreter.start()
# Files in /data persist across sessions
```

### Volume Operations

```python
# Commit changes to persistent storage
interpreter.commit()

# Reload to see changes from other containers
interpreter.reload()

# Upload local files to volume
interpreter.upload_to_volume(
    local_dirs={"./knowledge": "/knowledge"},
    local_files={"./config.json": "/config.json"}
)
```

## Secret Configuration

Secrets store sensitive values like API keys that are injected into the sandbox environment.

### Create a Secret

```bash
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=openai/gemini-3-flash-preview \
  DSPY_LLM_API_KEY=sk-...
```

### Secret Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `secret_name` | `LITELLM` | Default Modal Secret name |
| `secrets` | `[Secret.from_name("LITELLM")]` | List of Modal Secrets to inject |

### Required Secret Keys

For DSPy language model integration:

| Key | Required | Description |
|-----|----------|-------------|
| `DSPY_LM_MODEL` | Yes | Model identifier (e.g., `openai/gemini-3-flash-preview`) |
| `DSPY_LLM_API_KEY` | Yes | API key for the model provider |
| `DSPY_LM_API_BASE` | No | Custom API base URL |

## Timeout Settings

The interpreter supports multiple timeout configurations:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `timeout` | `600` (10 min) | Sandbox lifetime in seconds |
| `idle_timeout` | `None` | Idle timeout before sandbox termination |
| `execute_timeout` | Same as `timeout` | Timeout for individual `execute()` calls |
| `llm_call_timeout` | `60` | Timeout for sub-LLM calls within sandbox |

### Example: Custom Timeouts

```python
interpreter = ModalInterpreter(
    timeout=300,        # 5-minute sandbox lifetime
    execute_timeout=60, # 1-minute per code execution
    llm_call_timeout=30 # 30 seconds for llm_query calls
)
```

## Modal App Configuration

### App Name

The interpreter creates or looks up a Modal App by name:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `app_name` | `dspy-rlm-interpreter` | Modal App name for lookup/creation |

Apps are created automatically via `modal.App.lookup(app_name, create_if_missing=True)`.

### Sandbox Image

The default sandbox image uses Debian slim with Python 3.13:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `image_python_version` | `"3.13"` | Python version for sandbox |
| `image_pip_packages` | `("numpy", "pandas")` | Pre-installed packages |

### Custom Image

```python
import modal

custom_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "numpy", "pandas", "scikit-learn"
)

interpreter = ModalInterpreter(image=custom_image)
```

## Environment Variables Reference

### Authentication

| Variable | Description | Source |
|----------|-------------|--------|
| `MODAL_TOKEN_ID` | Modal authentication token ID | Env or `~/.modal.toml` |
| `MODAL_TOKEN_SECRET` | Modal authentication token secret | Env or `~/.modal.toml` |

### DSPy Language Model

| Variable | Description |
|----------|-------------|
| `DSPY_LM_MODEL` | Primary LM model identifier |
| `DSPY_LLM_API_KEY` | API key for the model provider |
| `DSPY_LM_API_BASE` | Custom API base URL |
| `DSPY_LM_MAX_TOKENS` | Maximum tokens for generation (default: 64000) |
| `DSPY_DELEGATE_LM_MODEL` | Delegate LM model for sub-agents |
| `DSPY_DELEGATE_LM_API_KEY` | API key for delegate LM |
| `DSPY_DELEGATE_LM_API_BASE` | Custom API base for delegate LM |

### Execution Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `WS_EXECUTION_MAX_TEXT_CHARS` | `65536` | Max characters in execution output |
| `WS_EXECUTION_MAX_COLLECTION_ITEMS` | `500` | Max items in collections |
| `WS_EXECUTION_MAX_RECURSION_DEPTH` | `12` | Max recursion depth for serialization |

### Runtime Settings

| Variable | Description |
|----------|-------------|
| `VOLUME_NAME` | Default volume name for persistence |
| `SECRET_NAME` | Default secret name for credentials |
| `FLEET_RLM_ENV_PATH` | Explicit path to `.env` file |

## Using Modal-backed Runtime

### Terminal Chat

```bash
# Basic usage
uv run fleet

# With volume persistence
uv run fleet --volume-name rlm-volume-dspy

# With custom secret
uv run fleet --secret-name MY-SECRET
```

### API Server

```bash
uv run fleet-rlm serve-api --port 8000
```

### MCP Server

```bash
uv run fleet-rlm serve-mcp --transport stdio
```

## Validate Runtime Connectivity

Use runtime diagnostics endpoints via UI or API:

```bash
# Check Modal connectivity
curl -X POST http://localhost:8000/api/v1/runtime/tests/modal

# Check LM connectivity
curl -X POST http://localhost:8000/api/v1/runtime/tests/lm

# Get runtime status
curl http://localhost:8000/api/v1/runtime/status
```

The status endpoint returns:

```json
{
  "modal_configured": true,
  "lm_configured": true,
  "active_models": ["openai/gemini-3-flash-preview"]
}
```

## Troubleshooting

### Modal Module Shadowing

If you see an error about `modal.py` shadowing the Modal package:

```
RuntimeError: Found ./modal.py which shadows the 'modal' package.
Rename/delete it and restart your shell or kernel.
```

**Solution**: Rename or delete any local `modal.py` file in your project directory.

### Missing Credentials

If Modal credentials are missing:

```
Modal credentials missing. Configure MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or run `modal setup`.
```

**Solution**: Run `uv run modal setup` or set the environment variables.

### Timeout Errors

If sandbox executions timeout:

```python
# Increase timeout values
interpreter = ModalInterpreter(
    timeout=900,        # 15-minute sandbox lifetime
    execute_timeout=120 # 2-minute per execution
)
```

---

See [Runtime Settings](runtime-settings.md) for local settings-write behavior.
