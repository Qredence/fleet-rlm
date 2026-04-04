---
name: rlm-debug
description: Debug fleet-rlm runtime issues from Claude Code. Use when diagnosing daytona_pilot failures, API and websocket contract problems, sandbox persistence bugs, or runtime readiness drift.
---

# RLM Debug — Runtime Diagnostics

Use this skill when the question is not "how do I use fleet-rlm?" but
"why is fleet-rlm not behaving correctly?"

## Runtime: `daytona_pilot`

`daytona_pilot` is the primary runtime. Daytona is the interpreter/sandbox backend.

## Canonical Checks

```bash
# from repo root
uv run fleet web
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
make test-fast
```

## Daytona Checks

```bash
env | grep DAYTONA
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
daytona version
```

Daytona durable storage should be inspected under `/home/daytona/memory/{memory,artifacts,buffers,meta}`. The live workspace is transient.

## Contract Checks

When symptoms involve the workspace UI, focus on these seams:

- `openapi.yaml`
- `/api/v1/runtime/*`
- `/api/v1/ws/chat`
- `/api/v1/ws/execution`

The riskiest backend files are:

- `src/fleet_rlm/api/routers/ws/stream.py` (live chat loop)
- `src/fleet_rlm/api/routers/ws/commands.py` (command dispatch)
- `src/fleet_rlm/api/routers/ws/turn_lifecycle.py` (run/turn lifecycle state)
- `src/fleet_rlm/api/runtime_services/settings.py` (settings routes)
- `src/fleet_rlm/api/runtime_services/diagnostics.py` (status/diagnostics)
- `src/fleet_rlm/runtime/execution/streaming.py` (streaming context)

## Common Failures

### Runtime mode mismatch

- Mismatch between requested `runtime_mode` and backend readiness state
- Fix by tracing `runtime_mode` through the initial websocket request and store state

### Daytona volume confusion

- Do not treat `DAYTONA_TARGET` as a workspace id or volume name
- Use the mounted durable volume rooted at `/home/daytona/memory`

### UI contract drift

- If backend request/response shapes changed, update `openapi.yaml` and re-run frontend API sync checks

### Sandbox-output confusion

- `sandbox_output` frames are transcript/debug traces
- `/api/v1/ws/execution` remains the canonical workbench stream

## Claude Code Delegation

- Use `rlm-specialist` for cross-runtime debugging and architecture fixes
- Use `daytona-runtime` for Daytona-specific volume and execution debugging
