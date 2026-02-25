# Deploying the API Server

This guide describes the current FastAPI server surface used by `fleet web` and API clients.

## Start the Server

```bash
uv run fleet-rlm serve-api --host 0.0.0.0 --port 8000
```

UI/static assets are served when frontend build output is present.

## Core Endpoint Groups

- Health: `/health`, `/ready`
- REST API: `/api/v1/*`
- WebSockets:
  - `/api/v1/ws/chat`
  - `/api/v1/ws/execution`

## API Docs Surface

When `scalar-fastapi` is available, interactive docs are served at:

- `/scalar`

## Runtime Configuration

You can pass Hydra overrides at startup:

```bash
uv run fleet-rlm serve-api --host 0.0.0.0 --port 8000 \
  interpreter.async_execute=true \
  agent.guardrail_mode=warn \
  rlm_settings.max_iters=8
```

## Auth and Environment Guardrails

Important runtime controls:

- `APP_ENV` (`local|staging|production`)
- `AUTH_MODE` (`dev|entra`)
- `AUTH_REQUIRED`
- `DATABASE_URL`
- `DATABASE_REQUIRED`
- `LEGACY_SQLITE_ROUTES_ENABLED`

See [Auth Modes](../auth.md) and [Database Architecture](../db.md).

## Smoke Checks

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/ready
```
