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

## Modal Credentials Missing

Symptom:

- Modal tests fail or sandbox startup errors

Fix:

```bash
uv run modal setup
```

Then re-run runtime tests:

- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`

## Secret/Volume Mismatch

Symptom:

- runtime says secret or volume unavailable

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

See [Auth Modes](../reference/auth.md).

## Planned vs Legacy Route Responses

- Planned scaffold routes under `/api/v1/{taxonomy|analytics|search|memory|sandbox}` return `501 Not Implemented`.
- Legacy-gated routes (`/api/v1/tasks*`, `/api/v1/sessions*` except `/state`) can return `410 Gone` when disabled.

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
