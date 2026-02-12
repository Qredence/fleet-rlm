# Changelog

All notable changes to this project are documented in this file.

## [0.4.1] - 2026-02-12

### Added

- Native PDF ingestion support via MarkItDown for document-processing flows.
- Trajectory-focused unit coverage for MCP passthrough, runner behavior, and ReAct command paths.

### Changed

- Set RLM trajectory metadata handling to default-on across runners, ReAct tooling, and MCP server surfaces.
- Updated CLI and Python API docs to reflect trajectory defaults and current command behavior.
- Hardened CI by pinning workflow Python to `3.12`.

### Fixed

- Improved resilience in ReAct tool handling around empty-exception code paths.

### Merged Pull Requests

- [#15](https://github.com/Qredence/fleet-rlm/pull/15): Align fleet-rlm trajectory handling with DSPy RLM API.
- [#16](https://github.com/Qredence/fleet-rlm/pull/16): Enable PDF ingestion and default-on RLM trajectory metadata.

## [0.4.0] - 2026-02-12

### Breaking Changes

- Removed the legacy Python interactive runtimes (Textual and prompt-toolkit).
- `fleet-rlm code-chat` is now OpenTUI-only.

### Added

- `docs/how-to-guides/using-claude-code-agents.md` for Claude Code workflows (skills, sub-agents, teams).
- `docs/reference/source-layout.md` documenting `src/fleet_rlm/` package structure.
- `docs/explanation/memory-topology.md` and moved memory-topology notes under `docs/explanation/memory-topology/`.

### Changed

- Updated package version to `0.4.0`.
- Simplified package dependencies for easier install/use with PyPI + `uv`.
- Updated CLI/docs to reflect OpenTUI-first/only interactive flow.
- Updated `AGENTS.md` to match current project conventions and runtime surfaces.

### Removed

- `src/fleet_rlm/interactive/textual_app.py`
- `src/fleet_rlm/interactive/legacy_session.py`
- `src/fleet_rlm/interactive/config.py`
- `src/fleet_rlm/interactive/session.py`
- `src/fleet_rlm/interactive/ui.py`
- `tests/ui/test_textual_app.py`

### Internal Cleanup

- Removed empty placeholder package directories under `src/fleet_rlm/`.
- Removed checked-in `__pycache__` directories under `src/fleet_rlm/`.
- Moved non-runtime memory-topology notes out of package source and into docs.
