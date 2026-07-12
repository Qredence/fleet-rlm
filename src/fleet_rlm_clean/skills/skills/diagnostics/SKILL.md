---
name: diagnostics
description: "Diagnose fleet_rlm_clean failures — Daytona leases, SSE chat, auth, budgets, and offline vs live kernel. Use when something is broken."
---

# Diagnostics (clean)

## Symptom → check

1. **"Sandbox / interpreter won't start"**
   - `FLEET_CLEAN_DAYTONA_API_KEY` set for live paths
   - `FLEET_CLEAN_LIVE_KERNEL` enabled when expecting live providers
   - Volume name/mount: `FLEET_CLEAN_VOLUME_NAME`, `FLEET_CLEAN_VOLUME_MOUNT_PATH`
   - Run `scripts/diagnose.py` (presence checks only; never print secrets)

2. **"Chat SSE returns auth error"**
   - `FLEET_CLEAN_AUTH_MODE=dev` → synthetic headers `X-Fleet-User-Id` / `X-Fleet-Workspace-Id`
   - `neon` → valid Neon Auth Bearer JWT; never use `DAYTONA_API_KEY` as Bearer
   - Unknown auth modes fail closed at settings load

3. **"Turn cancelled / timed out"**
   - Client cancel via runs API vs wall budget `max_wall_seconds`
   - Lease must release on cancel; sandbox must not be deleted by release

4. **"Skill load fails"**
   - Cards are metadata only; body requires `load_skill(skill_id)` after authorize
   - Hidden / foreign-workspace skills look like not found
   - Budget: `max_skill_loads`

5. **"Attachment / artifact tool fails"**
   - Invalid UUID ids → sanitized error
   - Ownership isolation (user/workspace) before read
   - Host roots: `FLEET_CLEAN_UPLOAD_ROOT`, `FLEET_CLEAN_ARTIFACT_ROOT`

6. **"Client sees secrets or provider internals"**
   - Bug: public paths must use sanitize/redact — never raw `str(exc)` from Daytona/LLM

7. **"Tests flaky offline"**
   - Prefer `tests/unit/clean_backend/`
   - Live lanes need explicit opt-in (`FLEET_CLEAN_LIVE` / live markers)

## Risky clean files

| File | Risk |
|------|------|
| `chat/turn_coordinator.py` | Isolation vs lease ordering |
| `rlm/runner.py` | Event projection, tool wiring, cancel |
| `daytona/session_manager.py` | Acquire/mount/lease lifecycle |
| `api/neon_auth.py` | Fail-closed JWT verification |
| `rlm/sanitize.py` | Client-safe error text |

## Test lanes

| Change | Command |
|--------|---------|
| Clean unit | `uv run pytest -q tests/unit/clean_backend` |
| Skills | `uv run pytest -q tests/unit/clean_backend/test_skill_cards.py tests/unit/clean_backend/test_skill_tools.py tests/unit/clean_backend/test_skill_loader.py` |
| Daytona adapter | `uv run pytest -q tests/unit/clean_backend/test_daytona_adapter.py tests/unit/clean_backend/test_session_manager.py tests/unit/clean_backend/test_volume_paths.py` |
| SSE / cancel | `uv run pytest -q tests/unit/clean_backend/test_chat_sse.py tests/unit/clean_backend/test_cancel_timeout_redact.py` |

## Operator script

```bash
uv run python src/fleet_rlm_clean/skills/skills/diagnostics/scripts/diagnose.py
```

Checks package import, key env **presence** (values redacted), and auth mode validity. Does not call live Daytona/LLM networks by default.

## See also

- **sandbox-execution** — interpreter and volume settings
- **rlm** — budgets and product surface
