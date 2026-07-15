---
name: diagnostics
description: "Diagnose fleet_rlm failures — Daytona Admission, Interpreter Leases, SSE chat, Turn Timeout, RLM Options, and runtime profiles. Use when something is broken."
---

# Diagnostics (clean)

## Symptom → check

1. **"Sandbox / interpreter won't start"**
   - `FLEET_DAYTONA_API_KEY` set for live paths
   - `FLEET_RUN_ENVIRONMENT=daytona` when expecting providers
   - Volume name/mount: `FLEET_VOLUME_NAME`, `FLEET_VOLUME_MOUNT_PATH`
   - Run `scripts/diagnose.py` (presence checks only; never print secrets)

2. **"Turn cancelled / timed out"**
   - Client cancel via runs API vs `turn_timeout_seconds`
   - Lease must release on cancel; sandbox must not be deleted by release

3. **"Skill load fails"**
   - Cards are metadata only; body requires `load_skill(skill_id)` after authorize
   - Hidden / foreign-workspace skills look like not found
   - Recheck authorization, expected version, and normalized resource path

4. **"Attachment / artifact tool fails"**
   - Invalid UUID ids → sanitized error
   - Ownership isolation (user/workspace) before read
   - Private test and Deno data root: `FLEET_DATA_ROOT`

5. **"Client sees secrets or provider internals"**
   - Bug: public paths must use sanitize/redact — never raw `str(exc)` from Daytona/LLM

6. **"Tests flaky offline"**
   - Prefer `tests/unit/backend/`
   - Live lanes need explicit opt-in (`FLEET_LIVE` / live markers)

## Risky clean files

| File | Risk |
|------|------|
| `chat/turn_coordinator.py` | Isolation vs lease ordering |
| `rlm/runner.py` | Event projection, tool wiring, cancel |
| `daytona/session_manager.py` | Acquire/mount/lease lifecycle |
| `api/local_scope.py` | Deterministic local namespace |
| `rlm/sanitize.py` | Client-safe error text |

## Test lanes

| Change | Command |
|--------|---------|
| Clean unit | `uv run pytest -q tests/unit/backend` |
| Skills | `uv run pytest -q tests/unit/backend/test_skill_cards.py tests/unit/backend/test_skill_tools.py tests/unit/backend/test_skill_loader.py` |
| Daytona adapter | `uv run pytest -q tests/unit/backend/test_daytona_adapter.py tests/unit/backend/test_session_manager.py tests/unit/backend/test_volume_paths.py` |
| SSE / cancel | `uv run pytest -q tests/unit/backend/test_chat_sse.py tests/unit/backend/test_cancel_timeout_redact.py` |

## Operator script

```bash
uv run python src/fleet_rlm/skills/skills/diagnostics/scripts/diagnose.py
```

Checks package import, key env **presence** (values redacted), and runtime settings. Does not call live Daytona/LLM networks by default.

## See also

- **sandbox-execution** — interpreter and volume settings
- **rlm** — RLM Options and product surface
