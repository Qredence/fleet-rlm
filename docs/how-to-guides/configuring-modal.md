# Configuring Modal

`fleet-rlm` executes code in Modal sandboxes. This guide covers the minimum setup.

## 1. Authenticate Modal

```bash
uv run modal setup
```

## 2. Create Runtime Volume

```bash
uv run modal volume create rlm-volume-dspy
```

## 3. Create Runtime Secret

```bash
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=openai/gpt-4o \
  DSPY_LLM_API_KEY=sk-...
```

Optional additional key:

- `DSPY_LM_API_BASE`

## 4. Use Modal-backed Runtime

Terminal chat:

```bash
fleet --volume-name rlm-volume-dspy --secret-name LITELLM
```

API server:

```bash
uv run fleet-rlm serve-api --port 8000
```

MCP server:

```bash
uv run fleet-rlm serve-mcp --transport stdio
```

## 5. Validate Runtime Connectivity

Use runtime diagnostics endpoints via UI or API:

- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`

See [Runtime Settings](runtime-settings.md) for local settings-write behavior.
