# Changelog

All notable changes to this project are documented in this file.

## Unreleased

## 0.4.5

### Highlights (User Impact)

- Major internal modularization: core driver/interpreter, ReAct agent, CLI, and terminal layers are now cleanly separated into focused modules.
- All delegate tools (`analyze_long_document`, `grounded_answer`, `triage_incident_logs`, etc.) now use true recursive sub-agents instead of single-shot RLM invocations, giving them full tool access and unified depth enforcement.
- TUI WebSocket robustness: binary frame handling and error propagation to transcript.

### Added

- **Change:** Added Ink mention-input debouncing via `MentionDebounceController` plus focused unit coverage for burst suppression and stale-response token invalidation.
  **Outcome:** `@` mention search now avoids request storms during typing and remains stable under rapid input changes.
- **Change:** Extracted `delegate_sub_agent.py` with shared `spawn_delegate_sub_agent()` helper for true recursive sub-agent spawning.
  **Outcome:** All delegate tools now use a single, consistent recursion pattern with depth enforcement.
- **Change:** Extracted agent mixins: `CoreMemoryMixin`, `DocumentCacheMixin`, `ValidationConfig`, and `ToolDelegationMixin` with dynamic `__getattr__` dispatch.
  **Outcome:** Agent class reduced from ~1000 to ~467 lines; 25+ boilerplate delegator methods replaced by dynamic dispatch.
- **Change:** Extracted core helpers into focused modules: `driver_factories.py`, `sandbox_tools.py`, `volume_tools.py`, `llm_tools.py`, `session_history.py`, `output_utils.py`, `volume_ops.py`.
  **Outcome:** Interpreter and driver are now focused on lifecycle/protocol; business logic lives in dedicated modules.
- **Change:** Extracted ReAct tools into focused modules: `document_tools.py`, `filesystem_tools.py`, `chunking_tools.py`, `tools_rlm_delegate.py`, `tools_memory_intelligence.py`, `tools_sandbox_helpers.py`.
  **Outcome:** Tool definitions are organized by domain instead of monolithic files.
- **Change:** Extracted CLI subcommands into `cli_commands/` and terminal helpers into `terminal/` subpackage.
  **Outcome:** CLI and terminal chat code is modular and easier to maintain.
- **Change:** Added `test_context_manager.py` for agent `__enter__`/`__exit__` lifecycle testing.
  **Outcome:** Context manager behavior is now covered by dedicated tests.

### Changed

- **Change:** Reworked `mentions.search` indexing/ranking with short-lived caching, scoped subtree lookup for path-prefix queries (for example `src/serv`), top-level suggestions on empty query, and common large-directory filtering (`.git`, `node_modules`, `.venv`, caches).
  **Outcome:** Mention suggestions are faster and more relevant on large repositories while reducing noisy/expensive scans.
- **Change:** Expanded mention-search regression coverage in bridge runtime and handler tests (cache reuse, ignore-list behavior, top-level empty-query behavior, scoped subtree routing, stdio method routing).
  **Outcome:** Higher confidence against mention-search regressions across bridge server and Ink clients.
- **Change:** Refreshed architecture/docs surfacing with updated diagrams and indexing, including performance-regression guide links and AGENTS quality/perf baseline command references.
  **Outcome:** Operators get clearer system topology context and easier discovery of performance guardrail workflows.
- **Change:** All RLM delegate tools (`analyze_long_document`, `summarize_long_document`, `extract_from_logs`, `grounded_answer`, `triage_incident_logs`, `plan_code_change`, `propose_core_memory_update`) and memory intelligence tools (`memory_tree`, `memory_action_intent`, `memory_structure_audit`, `memory_structure_migration_plan`, `clarification_questions`) now spawn true recursive sub-agents instead of single-shot `dspy.RLM` invocations.
  **Outcome:** Delegate tools have full ReAct tool access, unified depth tracking, and consistent delegation semantics.
- **Change:** TUI WebSocket handler (`useWebSocket.ts`) now handles binary/Blob frames with `decodeIncomingMessage` and `parseIncomingPayload` type guards.
  **Outcome:** WebSocket connections no longer crash on non-text frames.
- **Change:** TUI error events (`App.tsx`) now propagate to transcript as system messages and trigger `RESET_TURN`.
  **Outcome:** Errors are visible in the chat transcript and the input is re-enabled after failures.

### Fixed

- **Change:** Routed `mentions.search` through synchronous bridge dispatch (instead of async-only method handling) while keeping Ink RPC method alignment with bridge namespaces.
  **Outcome:** Mention-search requests reliably resolve to a valid handler in stdio bridge runtime instead of intermittent unknown-method behavior.
- **Change:** Reformatted `src/fleet_rlm/react/agent.py` and `src/fleet_rlm/react/tools_sandbox.py` to match Ruff formatter output.
  **Outcome:** CI `ruff format --check` passes consistently for the ReAct agent/tooling surface.
- **Change:** Corrected standalone terminal mention completion pattern to match non-whitespace suffixes after `@`.
  **Outcome:** `@` path suggestions now trigger reliably for common prefixes.
- **Change:** Updated bridge settings snapshot behavior to mask secret values by default in `values`, with explicit opt-in to include raw secrets.
  **Outcome:** Reduced accidental secret exposure in interactive settings payloads while preserving intentional debugging access.
- **Change:** Stabilized `notebooks/rlm-dspy-modal.ipynb` execution flow: canonical `fleet_rlm` imports, consolidated live-prerequisite gating, safer optional secret checks, and self-contained long-context analysis setup.
  **Outcome:** Notebook runs cleanly without stale import failures and no longer depends on cross-cell interpreter state.
- **Change:** Fixed all 15 pre-existing `ty check` type errors across 6 files (driver_factories, core_memory, runtime_factory, terminal/commands, terminal/settings).
  **Outcome:** `ty check src` passes with zero diagnostics.

## 0.4.4

### Highlights (User Impact)

- Safer Modal volume operations with path-boundary checks and stronger write/read consistency behavior.
- Better async runtime behavior via non-blocking interpreter execution in async server/streaming paths.
- Higher response quality visibility with configurable guardrails and streaming warning metadata.
- Simpler document/memory workflows through new convenience tools and command aliases.

### Added

- **Change:** Added async interpreter execution support via `ModalInterpreter.aexecute(...)` and async lifecycle context management (`__aenter__` / `__aexit__`).
  **Outcome:** Non-blocking async server/streaming paths with behavior parity to sync execution.
- **Change:** Added ReAct response guardrail controls with configurable modes (`off | warn | strict`) and warning propagation for low-substance responses and tool-error trajectories.
  **Outcome:** Improved response quality/safety with configurable strictness and clearer operator visibility.
- **Change:** Added persistent-volume convenience tools: `process_document`, `write_to_file`, and `edit_core_memory`.
  **Outcome:** Simpler, more ergonomic document + memory workflows for agent operations.
- **Change:** Added command aliases for `process_document`, `write_to_file`, and `edit_core_memory` in command dispatch.
  **Outcome:** These capabilities are uniformly available through command-driven UX paths.
- **Change:** Added unit coverage for async interpreter behavior, guardrails, command dispatch, and persistent memory tool behavior.
  **Outcome:** Lower regression risk and higher confidence in runtime/tooling stability.

### Changed

- **Change:** Modal runtime defaults now target Python 3.13 across config and interpreter defaults (`python:3.13-slim-bookworm`, `image_python_version="3.13"`).
  **Outcome:** Consistent runtime defaults across config and interpreter internals; reduced version drift.
- **Change:** Runtime wiring now propagates interpreter async and guardrail settings end-to-end through CLI/server/MCP/runners/agent construction.
  **Outcome:** Config changes now take effect consistently across all runtime entry points.
- **Change:** Streaming final payload now includes additive `guardrail_warnings` metadata while preserving existing trajectory/reasoning fields.
  **Outcome:** Better observability for quality issues without breaking existing payload consumers.

### Fixed

- **Change:** Hardened Modal volume path handling for sandbox tools and driver helpers to prevent path traversal outside mounted `/data`.
  **Outcome:** Tighter filesystem safety boundaries for volume-backed operations.
- **Change:** Improved volume consistency semantics for tool workflows with best-effort flush (`os.sync()` / `sync /data`) and explicit commit/reload integration (`commit()` on writes, `reload()` before reads).
  **Outcome:** More predictable persistence/read freshness across containers and sessions.

## [0.4.3] - 2026-02-15

### Highlights (User Impact)

- Packaging now reliably includes runtime Hydra config files.
- Type-checking consumers get explicit PEP 561 package typing support.

### Fixed

- **Change:** Added `fleet_rlm.conf` to setuptools package-data so Hydra config files (including `config.yaml`) are shipped in distributions.
  **Outcome:** Installed/PyPI builds no longer fail at startup with missing primary config errors.
- **Change:** Explicitly declared `py.typed` in package-data.
  **Outcome:** Downstream type checkers correctly detect and consume package type information.

## [0.4.2] - 2026-02-15

### Highlights (User Impact)

- ReAct agent alignment with DSPy primitives significantly improved reliability and composability.
- Recursive delegation is now safer (`max_depth`) and more compatible with DSPy 3.1.3 trajectories.
- WebSocket/session handling is more isolated and robust across tenants and restarts.
- API input handling now returns clearer client-facing 400 errors instead of generic server failures.

### Added

- **Change:** `RLMReActChatAgent` now subclasses `dspy.Module` with canonical `forward()`, and ReAct tools are explicitly wrapped as `dspy.Tool` (including auto-wrap for extra raw callables). (#18)
  **Outcome:** Better DSPy optimizer/module-graph compatibility and more reliable tool schema/function-calling behavior.
- **Change:** Added `rlm_query`, `edit_file`, `rlm_max_depth`, centralized recursion controls in `RlmSettings`, and depth tracking fields on the agent. (#19)
  **Outcome:** Recursive delegation and sandbox editing are available with bounded depth for safer execution.
- **Change:** Added DSPy 3.1.3 trajectory normalization helper and expanded async/streaming/core-memory test coverage (including API router regression tests).
  **Outcome:** More stable streaming/debug payloads and reduced regression risk in chat/task endpoints.
- **Change:** Added centralized Hydra config surface (`src/fleet_rlm/conf/`) and structured logging helper.
  **Outcome:** Cleaner runtime configuration and more consistent operational logging.

### Changed

- **Change:** Internal ReAct handle renamed from `self.agent` to `self.react`, and streaming references updated accordingly. (#18)
  **Outcome:** DSPy sub-module discovery is correct and maintenance/debug tooling is clearer.
- **Change:** Signature/output typing, tool list typing, and history helper usage were standardized.
  **Outcome:** Stronger type safety and more predictable runtime behavior.
- **Change:** Hydra-driven runtime wiring now consistently propagates `max_depth`, `max_iters`, and `rlm_max_llm_calls` through CLI/runners/server initialization.
  **Outcome:** Runtime limits and iteration controls are configurable from a single source instead of fragmented defaults.
- **Change:** Trajectory normalization now applies across sync and async streaming; pytest benchmark marker was registered.
  **Outcome:** More consistent trace output and cleaner test execution.

### Fixed

- **Change:** Fixed `rlm_query` answer extraction (`assistant_response`) and enforced recursion depth to prevent runaway delegation.
  **Outcome:** Correct sub-agent answers and bounded recursive execution.
- **Change:** Fixed DSPy 3.1.3 trajectory compatibility and async/streaming signature parity (`core_memory` propagation).
  **Outcome:** Cleaner, warning-free streaming behavior with more reliable tracing metadata.
- **Change:** Fixed session lifecycle issues (identity switch reset, manifest export for restart restore, reset cache cleanup, anonymous identity collision handling). (#23, #24)
  **Outcome:** Stronger tenant isolation and dependable session restore behavior.
- **Change:** Fixed API input handling for `/chat` and `/tasks/basic` to return 400 for client errors.
  **Outcome:** More accurate client-facing error semantics and easier debugging for callers.
- **Change:** Fixed FastAPI planner bootstrap compatibility for default and explicit `agent_model` paths.
  **Outcome:** More robust startup behavior across deployment configurations.

### Deprecated

- **Change:** Deprecated `load_rlm_settings()` in `core/config.py` in favor of unified Hydra config loading via `AppConfig.rlm_settings`.
  **Outcome:** Configuration now has a single authoritative loading path, reducing drift and ambiguity.

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

[0.4.3]: https://github.com/Qredence/fleet-rlm/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/Qredence/fleet-rlm/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Qredence/fleet-rlm/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Qredence/fleet-rlm/releases/tag/v0.4.0
