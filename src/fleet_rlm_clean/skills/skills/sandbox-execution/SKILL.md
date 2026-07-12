---
name: sandbox-execution
description: "Run code on Daytona code_interpreter leases with clean volume mounts. Use when executing Python in the sandbox, configuring FLEET_CLEAN volume settings, or debugging interpreter lifecycle."
---

# Sandbox execution (clean)

## Interpreter

Clean uses `DaytonaCodeInterpreter` over `sandbox.code_interpreter` (stateful REPL context), not `process.code_run`.

- **Start:** host acquires a lease (`session_manager`) with volume mount.
- **Execute:** `interpreter.execute(code)` → stdout string (or mapped adapter error).
- **Shutdown:** deletes the interpreter **context** only; lease release never deletes the sandbox.

Do not import `fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter` — that is the live package API.

## SUBMIT protocol (dspy.RLM)

- Finish a turn with `SUBMIT(answer=...)` matching `FleetRLMSignature`.
- Prefer serializable values (str, int, float, list, dict).
- Access results via attributes when the host unwraps predictions — not as a free-form dict protocol outside DSPy.

## Volume mount

Default mount path: `/home/daytona/fleet` (`FLEET_CLEAN_VOLUME_MOUNT_PATH`).

Default volume name: `rlm-volume-dspy` (`FLEET_CLEAN_VOLUME_NAME`).

Logical layout (see `references/volume-layout.md`):

| Area | Purpose |
|------|---------|
| `skills/` | Optional volume-side skill markdown |
| `attachments/` | Staged upload materialization when wired |
| `artifacts/` | Durable outputs when volume-written |
| `sessions/{session_id}/` | Session-scoped workspace |
| `sessions/{session_id}/runs/{run_id}/` | Per-run staging/artifacts |

Path ids for session/run segments must be UUID-shaped; host validates with `daytona.paths`.

## Settings that matter

| Env | Role |
|-----|------|
| `FLEET_CLEAN_DAYTONA_API_KEY` | Daytona client (SecretStr) |
| `FLEET_CLEAN_VOLUME_NAME` | Durable volume name |
| `FLEET_CLEAN_VOLUME_MOUNT_PATH` | Absolute sandbox mount |
| `FLEET_CLEAN_LIVE_KERNEL` | Allow live LM/Daytona wiring |

## Minimal host pattern

```python
from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter

# Backend is bound by session_manager to a leased sandbox's code_interpreter.
interpreter = DaytonaCodeInterpreter(backend=backend)
interpreter.start()
try:
    out = interpreter.execute("print(2 + 2)")
finally:
    interpreter.shutdown()
```

## Guardrails

- Treat sandbox FS outside the mount as ephemeral.
- Never echo raw provider errors, API keys, or stack traces to clients (host redacts).
- Prefer host `read_attachment` / `create_artifact` tools over inventing host paths inside the REPL.

## See also

- **volume-bootstrap** — directory contract under the mount
- **diagnostics** — lease / Daytona / auth failures
- **rlm** — budgets and SSE turn surface
