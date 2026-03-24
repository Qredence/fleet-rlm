---
name: rlm-debug
description: Debug fleet-rlm runtime issues from Claude Code. Use when diagnosing modal_chat or daytona_pilot failures, API and websocket contract problems, sandbox persistence bugs, or runtime readiness drift.
---

# RLM Debug — Runtime Diagnostics

Use this skill when the question is not "how do I use fleet-rlm?" but
"why is fleet-rlm not behaving correctly?"

## First Branch: Which Runtime?

- `modal_chat` means Modal is the interpreter backend
- `daytona_pilot` means Daytona is the interpreter backend

If the bug is Daytona-specific, also load `daytona-runtime`.

## Canonical Checks

```bash
# from repo root
uv run fleet web
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
make test-fast
```

## Modal Checks

```bash
uv run modal token set
uv run modal volume list
uv run python -c "import modal; print(modal.__version__)"
```

## Daytona Checks

```bash
env | grep DAYTONA
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

Daytona persistent memory should be inspected under `/home/daytona/memory`.

## Contract Checks

When symptoms involve the workspace UI, focus on these seams:

- `openapi.yaml`
- `/api/v1/runtime/*`
- `/api/v1/ws/chat`
- `/api/v1/ws/execution`

The riskiest backend files are:

- `src/fleet_rlm/api/routers/runtime.py`
- `src/fleet_rlm/api/routers/ws/*`
- `src/fleet_rlm/runtime/execution/streaming_context.py`

## Common Failures

### Runtime mode mismatch

- Frontend requests `daytona_pilot` but backend warnings/readiness assume Modal
- Fix by tracing `runtime_mode` through the initial websocket request and store state

### Daytona volume confusion

- Do not treat `DAYTONA_TARGET` as a workspace id or volume name
- Use the mounted workspace volume at `/home/daytona/memory`

### UI contract drift

- If backend request/response shapes changed, update `openapi.yaml` and re-run frontend API sync checks

### Sandbox-output confusion

- `sandbox_output` frames are transcript/debug traces
- `/api/v1/ws/execution` remains the canonical workbench stream

## Claude Code Delegation

- Use `rlm-specialist` for cross-runtime debugging and architecture fixes
- Use `modal-interpreter-agent` when the issue is Modal-only
