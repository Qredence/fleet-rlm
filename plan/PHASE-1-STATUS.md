# Phase 1 Status

Phase 1 has started by aligning the Daytona persistent volume layout with the planned durable session architecture.

> **Note:** Path references below use `/data/` as the logical volume root. The actual Daytona volume mount path is `/home/daytona/memory` (`DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH`); all paths are relative to that root.

## Implemented

- **Volume skeleton:** Daytona volume initialization now creates the existing runtime roots plus the Phase 1 durable roots:
  - `/data/memories`
  - `/data/knowledge/ingested`
  - `/data/knowledge/summaries`
  - `/data/skills/system`
  - `/data/skills/user`
  - `/data/sessions`
  - `/data/logs`
  - `/data/uploads`
- **Compatibility preserved:** Existing roots remain available:
  - `/data/memory`
  - `/data/artifacts`
  - `/data/buffers`
  - `/data/meta`
- **Storage path model:** `RuntimeStorageRoots` now exposes the new durable roots for future session, memory, skills, and knowledge work.
- **Session conversation path:** Websocket chat session manifests now write to `/data/sessions/<session_id>/conversation.json`.
- **Legacy restore fallback:** Session switching still reads the old `meta/workspaces/.../react-session-*.json` manifest path when the Phase 1 conversation file is absent.
- **Session workspace layout:** Session switching creates `/data/sessions/<session_id>/scratchpad/` and maps `/data/sessions/<session_id>/workspace` to the active Daytona working directory.
- **Non-live restore regression:** Unit coverage simulates a recreated Daytona session with the same mounted volume and verifies restored conversation state is imported into the agent.
- **Memory DB checkpoint:** Phase 1 owns `/data/memories/`; `/data/memories/core.db` initialization is deferred to Phase 2 memory tooling to avoid committing to a schema before memory tools exist.
- **Validation coverage:** Unit tests assert the planned skeleton is created without requiring live Daytona.
- **Live Daytona restore verification:** PASS (2026-05-26). Manifest written to `sessions/<session_id>/conversation.json`, scratchpad confirmed as directory, and manifest re-loaded from volume via `load_manifest_from_volume` with source and history turns matching. See `PHASE-1-PROGRESS.md` for full evidence.

## Phase 1 Complete

All Phase 1 targets are done. Next work is Phase 2 (memory tooling: `remember`/`recall`, `core.db` schema, migrations).
