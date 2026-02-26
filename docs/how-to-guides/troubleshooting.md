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
- Set `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` (or `DSPY_LM_API_KEY`)
- Restart terminal/session

## Modal Credentials Missing

Symptom:
- Modal tests fail or sandbox startup errors

Fix:

```bash
uv run modal setup
```

Then re-run server runtime tests:
- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`

## Secret/Volume Mismatch

Symptom:
- Runtime says secret or volume unavailable

Fix:

```bash
uv run modal secret create LITELLM DSPY_LM_MODEL=... DSPY_LLM_API_KEY=...
uv run modal volume create rlm-volume-dspy
```

## WebSocket Auth Failures

Symptom:
- WS close/errors on `/api/v1/ws/chat` or `/api/v1/ws/execution`

Checks:
- verify `AUTH_MODE` and `AUTH_REQUIRED`
- in dev mode, verify debug headers/token/query auth settings

See [Auth Modes](../auth.md).

## Legacy Routes Return 410

Symptom:
- `/api/v1/tasks*` or `/api/v1/sessions*` returns `410 Gone`

Cause:
- `LEGACY_SQLITE_ROUTES_ENABLED=false`

Action:
- expected in stricter environments; migrate to Neon-backed/runtime routes for production paths

## Diagnostic Commands

```bash
# CLI surface truth
uv run fleet-rlm --help
uv run fleet --help

# API route inventory
rg -n "^  /" openapi.yaml

# WS route inventory
rg -n "@router.websocket" src/fleet_rlm/server/routers/ws/api.py
```
