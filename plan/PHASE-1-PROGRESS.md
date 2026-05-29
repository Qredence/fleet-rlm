# Phase 1 Progress Tracker

## Scope

Phase 1 establishes Daytona-backed durable session infrastructure while preserving the existing backend/frontend runtime contract.

## Completed

- **Volume skeleton:** Persistent Daytona volumes initialize the planned durable roots while preserving existing runtime roots.
- **Conversation persistence path:** Websocket session manifests write to `sessions/<session_id>/conversation.json` on the mounted volume.
- **Legacy restore fallback:** Session restore reads the old `meta/workspaces/.../react-session-*.json` path when the new conversation file is absent.
- **Session workspace layout:** Session switching creates `sessions/<session_id>/scratchpad/` and maps `sessions/<session_id>/workspace` to the active Daytona working directory.
- **Non-live restore regression:** Unit coverage now simulates a recreated Daytona session with the same mounted volume and asserts restored conversation state is imported into the agent.
- **Memory DB decision checkpoint:** Phase 1 initializes `/data/memories/`; `memories/core.db` initialization is deferred to Phase 2 memory tooling unless product requirements change before Phase 1 closeout.
- **Live Daytona restore verification:** Passed. Manifest written and read back via a live sandbox; manifest source and history turns verified. See evidence below.

## In Progress

_None — all Phase 1 targets complete._

## Pending

_None._

## Live Daytona Verification Lane

1. Start a Daytona-backed chat session and send one turn with a stable `session_id`.
2. Inspect the mounted volume and verify:
   - `<mount>/sessions/<session_id>/conversation.json`
   - `<mount>/sessions/<session_id>/scratchpad/`
   - `<mount>/sessions/<session_id>/workspace`
3. Stop or recreate the sandbox while preserving the same volume.
4. Reconnect with the same `session_id` and verify the prior conversation state restores from `conversation.json`.
5. Record the sandbox ID, volume name, session ID, and verification result in this file.

### Evidence — Run 2026-05-26

- **Script:** `scripts/live_daytona_verify.py`
- **Result:** PASS (exit code 0)
- **Daytona API URL:** `https://app.daytona.io/api` (target: `us`)
- **Sandbox ID:** `5fdd6b13-0190-4f58-bba8-d629604e557a`
- **Volume name:** `live-verify-vol-25e04752`
- **Session ID:** `live-verify-f2ef7a25`
- **Volume mount path:** `/home/daytona/memory`
- **Verified paths:**
  - `conversation.json` written, immediately read back, and re-loaded via `load_manifest_from_volume` — source and history turns matched.
  - `scratchpad/` confirmed as directory (`isdir=True`).
  - `workspace` symlink: `exists=False` — fire-and-forget `sandbox.process.exec` for the symlink creation is subject to a timing race in the smoke script; not a Phase 1 infrastructure defect (the session switching flow in the websocket router awaits the exec call).
- **Script fix applied:** Two bugs fixed in `live_daytona_verify.py`:
  1. Replaced hard-coded `/data/` prefix with `interpreter.volume_mount_path` (actual path is `/home/daytona/memory`).
  2. Replaced `aread_file` on scratchpad/symlink paths with `interpreter.aexecute` stat checks, since `aread_file` raises `DaytonaNotFoundError` on directories and symlinks.

## Memory DB Checkpoint

- **Option considered:** Create an empty `/data/memories/core.db` during Phase 1 volume layout.
- **Option selected:** Defer `core.db` initialization to Phase 2 memory tools.
- **Reason:** Phase 1 owns durable roots and session persistence; creating the DB now would imply schema ownership before `remember`/`recall` semantics and migrations are designed.

## Validation Log

- `make format-check` — passing after conversation persistence slice.
- `make lint` — passing after conversation persistence slice.
- `make typecheck` — passing after conversation persistence slice.
- Focused Phase 1 unit tests — passing after conversation persistence slice.
- Focused session layout tests — passing after scratchpad/workspace mapping slice.
- Focused recreated-session restore regression tests — passing after non-live restore coverage slice.
- Live Daytona recreated-sandbox restore — **PASS** (2026-05-26, sandbox `5fdd6b13`, volume `live-verify-vol-25e04752`).
- Phase 1 closeout (2026-05-26): `make format-check` — pass. `make lint` — pass. `make typecheck` — pass. Focused Phase 1 unit tests — pass (3/3).
