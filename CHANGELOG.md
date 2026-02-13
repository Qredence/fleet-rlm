# Changelog

All notable changes to this project are documented in this file.

## [0.4.2] - 2026-02-13

### Added

- `RLMReActChatAgent` now subclasses `dspy.Module` with a canonical `forward()` entry point, enabling DSPy optimization, serialization, and module-graph discovery. (#18)
- All 16 react tools are explicitly wrapped with `dspy.Tool(func, name=..., desc=...)` for reliable function-calling schema generation. (#18)
- Extra tools passed as raw callables are auto-wrapped in `dspy.Tool`; pre-wrapped `dspy.Tool` instances are preserved as-is. (#18)
- 16 new unit tests covering `dspy.Module` subclass, `forward()`, `dspy.Tool` wrappers, `_get_tool` lookup, `list_react_tool_names`, and typed Signature generics.
- **`rlm_query` tool**: delegate complex sub-tasks to a recursive sub-agent with isolated history and state. (#19)
- **`edit_file` tool**: robust search-and-replace file editing in the sandbox with ambiguity detection. (#19)
- `rlm_max_depth` config setting (default: 3) for limiting recursive sub-agent depth. (#19)

### Changed

- Renamed internal `self.agent` → `self.react` on `RLMReActChatAgent` so the `dspy.ReAct` sub-module is discoverable via `named_sub_modules()`. (#18)
- Updated `streaming.py` references from `agent.agent` → `agent.react`. (#18)
- All bare `list` / `dict` output fields on DSPy Signatures now use typed generics (`list[str]`, `dict[str, str]`). (#18)
- `build_tool_list` return type annotation updated from `list[Callable]` to `list[Any]` to reflect `dspy.Tool` instances.

### Fixed

- **Session state leak on key switch**: agent is now reset when switching to a new workspace-user identity with no saved state, preventing history/document leakage across boundaries. (#23)
- **Session restore after restart**: exported session state is now included in the volume-persisted manifest, fixing silently broken session restoration on process restart. (#24)
- **Host-side document leak on reset**: `reset()` now clears `_document_cache`, `_document_access_order`, and `active_alias` in addition to history and sandbox buffers.
- **Cross-tenant identity collision**: unauthenticated WebSocket connections now receive a per-connection `anon-{uuid}` user identity instead of sharing `default:anonymous`, preventing state leakage between unrelated clients.

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
