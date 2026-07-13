# Troubleshooting

Common issues and current recovery paths.

## Planner Not Configured

Symptom:

- chat/API fails with planner configuration errors

Checks:

```bash
echo "$DSPY_LM_MODEL"
echo "$DSPY_LLM_API_KEY"
```

Fix:

- set `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` (or `DSPY_LM_API_KEY`)
- restart terminal/session

## Daytona Configuration Missing

Symptom:

- Daytona tests fail or sandbox startup errors

Fix:

```bash
echo "$DAYTONA_API_KEY"
echo "$DAYTONA_API_URL"
```

Then re-run runtime tests:

- `POST /api/v1/runtime/tests/daytona`
- `POST /api/v1/runtime/tests/lm`

## Daytona Volume or LM Configuration Mismatch

Symptom:

- runtime says volume or LM config unavailable

Fix:

```bash
echo "$DSPY_LM_MODEL"
echo "$DSPY_LLM_API_KEY"
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

## WebSocket Auth Failures

Symptom:

- WS close/errors on `/api/v1/ws/execution`

Checks:

- verify `AUTH_MODE` and `AUTH_REQUIRED`
- in dev mode, verify debug headers/token/query auth settings

See [Auth Modes](../reference/auth.md).

## Removed Deprecated/Planned Routes

- Deprecated and planned/stub REST surfaces were removed.
- Requests to `/api/v1/tasks*`, `/api/v1/sessions*` CRUD, and `/api/v1/{taxonomy|analytics|search|memory|sandbox}*` now return `404 Not Found`.
- The supported product surfaces are `Workbench`, `Volumes`, `Optimization`,
  and `Settings`. Retired `/app/taxonomy*`, `/app/skills*`, `/app/memory`,
  and `/app/analytics` paths now fall through to the frontend not-found route
  instead of remaining first-class pages or redirect shims.

## Diagnostic Commands

```bash
# CLI surface truth
uv run fleet-rlm --help
uv run fleet --help

# API route inventory
rg -n "^  /" openapi.yaml

# WS route inventory
rg -n "@router.websocket" src/fleet_rlm/api/routers/ws/endpoint.py
```
