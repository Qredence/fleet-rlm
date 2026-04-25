# fleet-rlm v0.4.94 Release Notes

Release date: 2026-03-03

## Highlights

- WS-first migration completed by removing remaining deprecated HTTP chat compatibility surfaces.
- Execution tracing internals were simplified for more maintainable websocket and timeline event flows.
- Packaging now guarantees published wheels include synchronized frontend assets, so `fleet web` installs ship with current UI.

## Added

- Added deterministic end-to-end execution canvas smoke coverage (`execution-canvas-smoke.spec.ts`) for lane labels, elapsed timing, and untruncated output rendering.
- Added release artifact integrity validation via `scripts/check_wheel_frontend_sync.py` and integrated it into release workflows.

## Changed

- Refactored websocket chat internals into smaller helpers under `src/fleet_rlm/api/routers/ws/` while preserving endpoint contracts.
- Refactored execution event/citation shaping helpers (`step_builder.py`, `streaming_citations.py`, `streaming.py`) for clearer event construction.
- Aligned backend/frontend docs and contract tests with current WS-first routes and OpenAPI-generated surfaces.
- Updated build workflow so `uv build` runs frontend bundling in source/release contexts, and local `fleet web` prefers `src/frontend/dist` when present.
- Mounted frontend `branding/` static assets in FastAPI so logo and brand assets are served as static files (not SPA fallback HTML).

## Removed

- Removed deprecated HTTP chat router and legacy frontend compatibility exports/types tied to `/api/v1/chat`.

## Installation

```bash
uv tool install fleet-rlm
fleet web
```

## Merged PRs

- [#94](https://github.com/Qredence/fleet-rlm/pull/94): Remove deprecated HTTP chat compatibility and related cleanup.
- [#95](https://github.com/Qredence/fleet-rlm/pull/95): Filesystem UI and follow-up fixes across frontend/backend surfaces.
