# fleet-rlm v0.4.99 Release Notes

Release date: 2026-03-22

## Highlights

- Reorganized the backend into clearer `runtime/`, `integrations/`, `api/`, `cli/`, and `scaffold/` ownership boundaries.
- Tightened the shared WebSocket and workbench contract while keeping Modal and Daytona on the same ReAct + DSPy runtime backbone.
- Removed retired frontend route shims and browser-token compatibility paths so the supported shell surface stays narrow and explicit.
- Refreshed repo docs, validation scripts, and release metadata so contributor guidance matches the current implementation.

## Breaking Changes

- Removed the older internal module layout under `fleet_rlm.core`, `fleet_rlm.features`, `fleet_rlm.infrastructure`, and `fleet_rlm.conf`, plus compatibility façades like `fleet_rlm.runners`.
- Removed the retired `/app/skills*`, `/app/memory`, `/app/analytics`, and `/app/taxonomy*` route shims. Legacy URLs now fall through to `/404`.
- Removed browser auth compatibility for the legacy `fleet_access_token` localStorage key. The canonical `fleet-rlm:access-token` key is now the supported path.

## Added

- Added a modular websocket transport under `src/fleet_rlm/api/routers/ws/*` with focused endpoint, completion, artifacts, persistence, terminal, and HITL helpers.
- Added canonical backend package homes for runtime, provider, scaffold, and terminal responsibilities under `src/fleet_rlm/runtime/*`, `src/fleet_rlm/integrations/*`, and `src/fleet_rlm/cli/terminal/*`.
- Added frontend OpenAPI drift checks and broader test coverage for runtime factory assembly, websocket lifecycle paths, execution summaries, and Daytona behavior.

## Changed

- Reworked FastAPI bootstrap, dependency wiring, runtime services, and websocket routing to match the new package layout.
- Kept `/api/v1/ws/execution` as the canonical workbench stream and tightened frontend hydration around execution summaries instead of broad final-message scraping.
- Folded the Daytona workbench into the main workspace shell so experimental runtime sessions feel like part of the main product rather than a parallel UI.
- Updated docs, architecture maps, AGENTS guidance, and release metadata to match the current repo structure and supported surfaces.

## Fixed

- Hardened streaming error handling, delegate completion fallbacks, and Daytona cancellation/runtime state paths.
- Fixed multiple frontend regressions around workbench rendering, citations, canvas behavior, and shell state.
- Improved metadata consistency across release validation, OpenAPI artifacts, and repo documentation.

## Installation

```bash
uv tool install fleet-rlm==0.4.99
fleet web
```

## Merged PRs

- [#107](https://github.com/Qredence/fleet-rlm/pull/107): Add Daytona source staging and refresh workspace UX.
