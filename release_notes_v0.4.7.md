## fleet-rlm 0.4.7

This release improves the Web UI-first workflow with runtime settings/diagnostics support, WebSocket hardening, and docs alignment, while also including maintenance-only code quality cleanup.

### Highlights

- Added runtime settings and connectivity diagnostics support for local Web UI workflows.
- Hardened WebSocket chat/execution behavior for more reliable browser sessions and execution stream delivery.
- Continued documentation and frontend/runtime integration cleanup to align with the current Web UI-first experience.

### Added

- Runtime settings + diagnostics backend support under `/api/v1/runtime/*` for local LM/Modal configuration and connectivity checks.
- Frontend runtime settings integration and runtime health warning surfacing in the skill creation flow.
- Additional coverage around runtime settings, WebSocket helpers/routes, and trajectory payload behavior in the changed paths.

### Changed

- Documentation and developer guidance were refreshed to better match current runtime settings, WebSocket behavior, and Web UI-first usage.
- Frontend/backend integration surfaces were cleaned up (including trajectory payload and legacy bridge path cleanup) to reduce maintenance overhead.
- Maintenance-only code quality cleanup (CodeQL/static-analysis findings) improved readability in TUI scripts/helpers without intended user-facing behavior changes.

### Fixed

- WebSocket `/api/v1/ws/chat` and `/api/v1/ws/execution` flows were hardened, including local persistence-wrapper regression handling and bounded internal step emission.
- Local debug script naming was updated to avoid accidental pytest collection conflicts.

### Merged Pull Requests

- [#67](https://github.com/Qredence/fleet-rlm/pull/67): Refactor/code-quality cleanup for readability and static-analysis findings.
- [#72](https://github.com/Qredence/fleet-rlm/pull/72): Frontend runtime settings, websocket hardening, and docs sync.
