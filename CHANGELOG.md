# Changelog

All notable changes to this project are documented in this file.

## Unreleased

- The notes below summarize the current uncommitted worktree on top of `0.4.99`.

### Highlights (User Impact)

- Reworked settings into a reusable in-shell dialog/sheet that can open directly from the command palette, sidebar, and runtime warning CTAs without forcing a full route transition away from the active workspace.
- Split the oversized settings screen into shared settings-content and event modules, so the routed `/settings` entrypoint and the shell overlay now share one implementation path.
- Tightened shell presentation by simplifying the workspace empty state, reordering sidebar actions, removing redundant session-row chrome, and aligning switch/sidebar typography with the current UI token set.

### Added

- **Change:** Added reusable frontend modules for `SettingsDialog`, settings-section helpers/content, `open-settings` event dispatch, and a breadcrumb primitive for dialog navigation chrome.
  **Outcome:** Settings can now render as a desktop dialog or mobile bottom sheet from anywhere in the shell while keeping one shared implementation for section state and copy.
- **Change:** Added focused frontend tests covering settings-event dispatch semantics and the simplified sidebar/empty-state expectations.
  **Outcome:** The new in-shell settings flow has regression coverage around dialog handoff, focus-return plumbing, and the lighter shell presentation.

### Changed

- **Change:** Updated command palette actions, workspace runtime settings entrypoints, and the sidebar settings button to request the shared settings dialog before falling back to route navigation.
  **Outcome:** Operators can open settings in context, jump straight to runtime controls when prompted, and return focus to the initiating control after closing the overlay.
- **Change:** Converted the routed settings screen into a thin wrapper around the shared dialog content and moved section metadata/rendering into `settings-content`.
  **Outcome:** The `/settings` route and shell overlay stay visually and functionally aligned without duplicating large amounts of settings UI logic.
- **Change:** Refreshed base shell/UI primitives, including the Base UI switch wrapper, sidebar group-label typography, and desktop settings header chrome.
  **Outcome:** Settings and shell surfaces read more consistently with the current design system and tokenized typography.

### Removed

- **Change:** Removed the dedicated routed settings-page chrome, the workspace empty-state "Operator workspace" badge, and the redundant conversation icon in saved-session rows.
  **Outcome:** The workspace shell is lighter, and settings no longer feel like a separate full-page surface.

## [0.4.99] - 2026-03-22

### Highlights (User Impact)

- Rebased the backend onto a clearer package layout built around `runtime/`, `integrations/`, and `scaffold/`, retiring the older `core/`, `features/`, `infrastructure/`, and `conf/` seams.
- Split the websocket and runtime surfaces into smaller focused modules, tightened the shared execution-stream contract, and kept Modal and Daytona on the same ReAct + DSPy runtime backbone.
- Shipped a breaking frontend cleanup that removes retired route shims, drops the legacy browser token migration path, and prunes high-confidence dead files and dependencies while keeping the supported shell surface narrow.
- Refreshed the repo’s docs, AGENTS guides, codebase maps, release metadata, and validation scripts so the published guidance matches the current implementation.

### Breaking Changes

- **Change:** Removed the legacy internal Python module layout under `fleet_rlm.core`, `fleet_rlm.features`, `fleet_rlm.infrastructure`, `fleet_rlm.conf`, plus root compatibility façades like `fleet_rlm.runners` and `fleet_rlm.scaffold`.
  **Outcome:** Internal imports must move to the canonical `fleet_rlm.runtime`, `fleet_rlm.integrations`, `fleet_rlm.scaffold`, `fleet_rlm.cli`, and `fleet_rlm.api` modules.
- **Change:** Removed the retired `/app/skills*`, `/app/memory`, `/app/analytics`, and `/app/taxonomy*` redirect shims.
  **Outcome:** Old legacy product URLs now resolve through the not-found flow instead of silently redirecting into supported surfaces.
- **Change:** Removed browser auth compatibility for the legacy `fleet_access_token` localStorage key.
  **Outcome:** Only the canonical `fleet-rlm:access-token` storage contract is supported going forward.
- **Change:** Removed the `fleet_rlm.cli.runtime_builders` module and the `fleet_rlm.cli.terminal` re-export surface.
  **Outcome:** Callers now import the canonical runtime factory and terminal helper modules directly.

### Added

- **Change:** Added canonical backend package homes for the current architecture: `src/fleet_rlm/runtime/*`, `src/fleet_rlm/integrations/*`, `src/fleet_rlm/scaffold/*`, `src/fleet_rlm/cli/runtime_factory.py`, and a focused `src/fleet_rlm/cli/terminal/*` module set.
  **Outcome:** Runtime, provider, scaffold, and terminal responsibilities now live in stable namespaces instead of being scattered across multiple transitional package trees.
- **Change:** Added a fully modular websocket transport under `src/fleet_rlm/api/routers/ws/*`, including focused endpoint, stream, completion, artifacts, persistence, task-control, terminal, and HITL helpers.
  **Outcome:** The shared chat/execution transport is easier to reason about, test, and extend without routing all behavior through a single oversized websocket module.
- **Change:** Added new backend/provider modules for observability bootstrap, Daytona integration, runtime services, and package-local configuration/database/MCP ownership.
  **Outcome:** MLflow/PostHog, provider wiring, persistence, and server startup are easier to trace to their owning packages.
- **Change:** Added an app-shell catchall route at `/app/$` plus an explicit frontend OpenAPI drift checker in `src/frontend/scripts/check-api-sync.mjs`.
  **Outcome:** Retired app routes now fail cleanly to `/404`, and frontend API validation now checks generated-asset drift without breaking on intentional staged changes.
- **Change:** Added broad new unit/integration coverage for runtime factory assembly, websocket completion/errors/HITL paths, execution summaries, Daytona runtime/provider behavior, and the tightened frontend workbench contract.
  **Outcome:** The refactor is backed by stronger test coverage across the most volatile runtime and transport seams.

### Changed

- **Change:** Reorganized the backend around the shared ReAct + DSPy runtime contract, moving agent/session/execution/content/tool logic into `runtime/*` and provider/config/database/observability concerns into `integrations/*`.
  **Outcome:** The codebase now matches the documented architecture more closely, with clearer ownership boundaries and fewer duplicate implementation paths.
- **Change:** Reworked FastAPI bootstrap, dependency wiring, runtime services, and websocket routing to use the new package layout and focused helper modules.
  **Outcome:** Server startup, diagnostics, settings, traces, and websocket session lifecycle code are more modular and easier to validate.
- **Change:** Kept `/api/v1/ws/execution` as the canonical workbench stream while preserving rich live chat updates, and tightened frontend workbench hydration around execution summaries instead of broad chat-final scraping.
  **Outcome:** Transcript and workbench behavior stay aligned across runtime modes while reducing ambiguous hydration paths.
- **Change:** Aligned Daytona-specific behavior to the shared runtime architecture and kept `DaytonaWorkbenchChatAgent` as the focused Daytona-specific agent layer over that shared runtime.
  **Outcome:** Modal and Daytona continue to share one conversational/runtime model, with Daytona-specific logic isolated to the provider layer instead of a parallel orchestration stack.
- **Change:** Bumped the canonical release metadata and API schema versioning to `0.4.99` across package metadata, runtime version exports, OpenAPI documents, generated frontend API types, and the lockfile.
  **Outcome:** Build metadata, generated clients, and runtime introspection stay aligned on the new release number.
- **Change:** Reworked frontend route handling, shell navigation truth, and smoke coverage so unsupported legacy URLs fall through to `/404` instead of participating in shell navigation mappings.
  **Outcome:** The supported app surface is now explicitly limited to `RLM Workspace`, `Volumes`, and `Settings`.
- **Change:** Narrowed frontend auth storage behavior to the canonical session/local migration path only and updated the related auth/unit coverage.
  **Outcome:** The browser auth layer is easier to reason about and no longer carries a stale migration branch for obsolete storage keys.
- **Change:** Updated the shared Base UI/input primitives, sidebar/workbench shell wiring, runtime warning flows, and execution-stream client behavior in the frontend.
  **Outcome:** The supported workspace shell stays aligned with the simplified product surface and the current execution/workbench contract.
- **Change:** Refreshed architecture/reference/how-to docs, AGENTS files, ADRs, codebase maps, and historical audit notes to match the current package layout and product/runtime contract.
  **Outcome:** Repo guidance now reflects the actual implementation rather than the older transitional structure.

### Fixed

- **Change:** Fixed release-check/frontend-check behavior by replacing the old git-diff-based frontend API drift gate with a pre/post sync validator.
  **Outcome:** `pnpm run api:check`, `make quality-gate`, and `make release-check` now work correctly when generated OpenAPI artifacts are intentionally part of the current change.
- **Change:** Fixed environment/config/runtime rough edges across bootstrap, MLflow/PostHog integration, DB helpers, volume routing, and websocket/runtime settings coverage.
  **Outcome:** Runtime validation and transport flows are more predictable during both local development and release verification.
- **Change:** Fixed route/auth/workbench regression coverage around retired routes, execution websocket identity, execution summary shaping, and compatibility fallbacks.
  **Outcome:** The simplified frontend surface and shared execution contract are less likely to drift silently.

### Removed

- **Change:** Deleted dead frontend route shims, mock-state files, prompt preference state, CodeMirror helpers, and unreferenced shared UI primitives identified by the current cleanup scan.
  **Outcome:** The frontend tree is smaller and clearer, with less dead code competing with the supported product surface.
- **Change:** Removed the now-unused frontend dependency set tied to those dead files and legacy UI paths.
  **Outcome:** Install/build surface area is reduced and dependency intent is clearer.
- **Change:** Removed thin Python compatibility wrappers in favor of direct imports from `fleet_rlm.cli.runtime_factory` and concrete terminal helper modules.
  **Outcome:** Backend CLI/runtime ownership is clearer and less dependent on legacy alias modules.
- **Change:** Removed the duplicate scaffold/skill assets from the old `.claude` and `features/scaffold` locations in favor of the canonical packaged scaffold tree.
  **Outcome:** There is now one supported bundled scaffold surface for distributed skills, hooks, agents, and teams instead of multiple competing copies.

## [0.4.98] - 2026-03-17

### Highlights (User Impact)

- Tightened the release packaging story for `0.4.98` by aligning package metadata, runtime version exports, OpenAPI contracts, generated frontend API types, and lockfile state on the same release number.

### Breaking Changes

- **Change:** No breaking API, CLI, runtime-behavior, or frontend contract changes were introduced in `0.4.98`.
  **Outcome:** Upgrading from `0.4.97` to `0.4.98` remains a release-metadata and packaging-consistency update rather than a compatibility migration.

### Changed

- **Change:** Bumped the canonical package/version sources to `0.4.98` in `pyproject.toml`, `src/fleet_rlm/__init__.py`, and `uv.lock`.
  **Outcome:** Build metadata, editable-install state, and runtime introspection now report the same release number consistently.
- **Change:** Updated the canonical and frontend OpenAPI documents to `0.4.98` and re-synced generated frontend client typings from the refreshed spec snapshot.
  **Outcome:** Backend schema metadata and frontend API artifacts now stay aligned for this release instead of advertising an older version.
- **Change:** Added a dedicated `0.4.98` changelog section to make the release metadata bump explicit in project history.
  **Outcome:** Contributors and release tooling now have a clear, validator-friendly record of what shipped in `0.4.98`.

### Fixed

- **Change:** Fixed release metadata drift between package configuration, runtime `__version__`, OpenAPI `info.version`, and generated frontend API defaults.
  **Outcome:** Version checks, generated client surfaces, and user-visible metadata now agree on `0.4.98` instead of mixing release identifiers.
- **Change:** Fixed the health-schema/default-version exposure in generated API artifacts to reflect the current release number.
  **Outcome:** Consumers inspecting generated types or OpenAPI-derived defaults now see the active release version rather than stale `0.4.97` metadata.

## [0.4.97] - 2026-03-13

### Highlights (User Impact)

- Expanded the experimental Daytona runtime so a run can now start from a repo, staged local context files/directories, both together, or reasoning-only input, with better live visibility into recursive child work.
- Added a dedicated run-inspection experience across the workspace with grouped execution traces, message-inspector tabs, and a clearer support rail for recursive RLM sessions.
- Upgraded the chat composer and runtime controls with per-turn execution mode selection plus cleaner runtime metadata, so operators can decide when to force deep RLM delegation versus normal tool use.
- Published a comprehensive documentation refresh covering setup, testing, deployment, runtime settings, APIs, architecture diagrams, ADRs, and frontend module structure.
- Added MLflow tracing, export, evaluation, and scorer workflows to make offline analysis and DSPy optimization easier.
- Continued the frontend cleanup/polish pass by removing unused surfaces and improving navigation, conversation history, loading states, and builder/workbench chrome.
- Unified the experimental Daytona pilot with the main workspace so operators can stay on the shared chat surface while still opening the full run workbench and inspection tools when needed.
- Routed Daytona pilot chat through the shared runtime pipeline, reducing special-case behavior and making runtime metadata, streaming, and source staging work more consistent across modes.
- Hardened long-running Daytona sessions by stabilizing workbench stream updates and preserving session state when terminal-backed runs fail or disconnect.
- Continued workspace polish with modularized chat/input rendering, smoother loading states, and frontend tooling updates for the shared Daytona flow.

### Added

- **Change:** Added Daytona workbench/store/type adapters, message-inspector tabs for evidence/execution/graph/trajectory views, a dedicated `RunWorkbench` surface, and a new conversation-history panel.
  **Outcome:** Operators can inspect recursive Daytona state, child nodes, evidence, execution details, and prior conversations from a more coherent workspace support rail.
- **Change:** Added Daytona CLI/WebSocket support for optional `repo_url`, optional `context_paths`, and reasoning-only turns, plus sandbox prompt management and host-side document ingestion for PDFs and supported office/web documents.
  **Outcome:** The experimental runtime can analyze local supporting material or pure reasoning tasks without requiring every session to begin from a cloned repository.
- **Change:** Added an execution-mode dropdown to the chat composer, dedicated runtime mode types, and more explicit chat/runtime state wiring.
  **Outcome:** Operators can choose `Auto`, `RLM only`, or `Tools only` behavior per turn and see runtime context reflected more clearly in the UI contract.
- **Change:** Added MLflow trace export/evaluation/optimization scripts and integrated RLM scorers into analytics workflows.
  **Outcome:** Contributors now have a clearer path for offline evaluation, trace correlation, and DSPy optimization loops.
- **Change:** Added substantial docs/reference material including developer setup, testing strategy, deployment, CLI/API/WebSocket references, frontend architecture notes, ADRs, and architecture/data-flow diagrams.
  **Outcome:** Onboarding and day-to-day reference coverage are much stronger across backend, frontend, and operational surfaces.

### Changed

- **Change:** Reworked `ChatMessageList` and related AI Elements primitives to coalesce reasoning, task, tool, sandbox, and final-output events into richer grouped trace rows.
  **Outcome:** Long-running chat traces are easier to scan and no longer fragment important execution context across many small rows.
- **Change:** Updated the builder/workbench shell with animated artifact tabs, clearer support-rail framing, refreshed header/navigation styling, improved mobile/desktop panel controls, and more intentional composer chrome.
  **Outcome:** The workspace is easier to navigate and feels more cohesive across run inspection, artifact browsing, and chat composition.
- **Change:** Completed more of the frontend ownership cleanup by moving live chat modules into canonical `features/rlm-workspace/*` and `src/stores/*` homes while continuing to generalize Daytona-specific helpers into shared run-workbench/repo/source utilities.
  **Outcome:** The active workspace architecture is clearer and less dependent on temporary transition seams while refactoring continues.
- **Change:** Updated the ReAct chat runtime and delegate-sub-agent flow to apply execution mode per turn, filter tool availability accordingly, and stream child-RLM work through the existing websocket trajectory pipeline.
  **Outcome:** Deep symbolic turns are easier to reason about live, and recursive delegation can now be forced or disabled without changing the top-level chat runtime.
- **Change:** Updated Daytona streaming/runtime wiring to emit bootstrapping status earlier, stream structured context-source metadata, improve cancellation handling, and use `daytona_mode="recursive_rlm"` consistently.
  **Outcome:** Daytona sessions communicate setup state more clearly and frontend consumers get a cleaner contract for recursive run inspection.
- **Change:** Improved runtime/router behavior with HTTP identity requirements, eager JSON serialization, extended async volume reload support, and stronger token/runtime-mode storage coverage.
  **Outcome:** Runtime APIs respond more predictably and related UI state is safer across reloads and session changes.
- **Change:** Refreshed docs/architecture guidance to reflect the Daytona pilot, CLI integration, snapshot archival, and historical cross-linking fixes.
  **Outcome:** Project guidance matches the current runtime structure more closely and stale links are reduced.
- **Change:** Folded the Daytona workbench into `RlmWorkspace`, removed separate Daytona/source setup cards, and refreshed the empty/support states across the shared workspace shell.
  **Outcome:** Experimental runtime sessions feel like part of the main product instead of a parallel UI while still keeping detailed inspection surfaces close at hand.
- **Change:** Reworked the Daytona backend around a shared chat agent/runtime path and updated API/runtime schemas accordingly.
  **Outcome:** Modal and Daytona flows now share more of the same chat/runtime contract, which lowers integration drift and makes the pilot easier to extend.
- **Change:** Modularized workspace chat/input rendering, refreshed shimmer/loading behavior, and upgraded frontend tooling to Vite 8 with `@xyflow/react`.
  **Outcome:** The workspace UI is easier to maintain and feels smoother during loading, long trace-heavy sessions, and graph-heavy views.
- **Change:** Documented the shared chat/workbench flow and added opencode Daytona plugin configuration for contributors.
  **Outcome:** Day-to-day setup and onboarding are clearer for the current experimental runtime path.

### Fixed

- **Change:** Improved pending-event draining in streaming internals, raised explicit errors when delegate streaming completes without a prediction, and tightened Daytona cancellation/security handling.
  **Outcome:** Child-RLM failures surface earlier and long-running trace streams are less likely to stall or linger in a confusing state.
- **Change:** Fixed inline citation URL handling plus follow-up null-safety/export issues across AI Elements and artifact helpers.
  **Outcome:** Citations resolve to the correct targets more reliably and richer workspace components are less brittle when payloads are partial.
- **Change:** Repaired MLflow metadata merging/trace lookup issues, frontend button and execution-canvas regressions, mobile canvas cleanup leaks, and several documentation formatting/link problems.
  **Outcome:** Analytics traces are more trustworthy, workspace UI regressions are reduced, and project docs are easier to navigate without broken references.
- **Change:** Preserved Daytona session state when terminal-backed runs fail and tightened stream-state handling in the workbench adapter/runner.
  **Outcome:** Recovery after runtime hiccups is more predictable, and the workbench is less likely to lose or mis-order run state.
- **Change:** Hardened MLflow/PostHog config initialization plus follow-up metadata/config typing and release metadata fixes.
  **Outcome:** Analytics setup is less brittle, and runtime/release metadata stay more consistent across environments.

### Removed

- **Change:** Removed the legacy mock skill-creation simulation stack, unused taxonomy graph modules, placeholder settings panes, stale feature-chat/artifact components, duplicated Daytona-only helper paths, orphan docs readmes, and outdated audit/code-health snapshots.
  **Outcome:** Smaller frontend/documentation surface area with less dead code, lower maintenance overhead, and fewer competing "canonical" paths to keep in sync.

### Merged Pull Requests

- [#107](https://github.com/Qredence/fleet-rlm/pull/107): Add Daytona source staging and refresh workspace UX.

## [0.4.95] - 2026-03-05

### Highlights (User Impact)

- Replaced recursive delegate sub-agent flows for long-context and memory-intelligence tools with cached runtime RLM modules, reducing orchestration overhead while preserving tool response contracts.
- Simplified package/API surface by removing deprecated `fleet_rlm.stateful` exports and implementation modules that were no longer part of the active runtime path.
- Hardened delegate result normalization and fallback metadata so tool outputs remain machine-consumable even when runtime modules return loosely typed payloads.
- Introduced a new chat composer stack (`ChatInput`) with attachment chips, agent selection, and per-message "Think" trace toggling wired into WS message payloads.
- Improved WebSocket startup failure UX by surfacing actionable Modal auth guidance when malformed token credentials are detected.
- Synced release/version metadata to `0.4.95` and tightened core runtime dependency bounds to reduce resolver ambiguity across install extras.

### Added

- **Change:** Added frontend script `lint:robustness` as an explicit alias for lint gate execution.
  **Outcome:** Frontend lint entrypoints are clearer and easier to reuse across CI/local workflows.
- **Change:** Added modular chat composer components under `src/frontend/src/components/chat/` (`ChatInput`, prompt-input primitives, attachment/agent/settings controls).
  **Outcome:** The skill-creation composer now supports richer controls while keeping input UX composable and testable.
- **Change:** Added dedicated regex helper module `src/fleet_rlm/utils/regex.py`.
  **Outcome:** Regex utilities now have a clearer, purpose-specific import path decoupled from broader tool helper surfaces.

### Changed

- **Change:** Refactored `react/tools/delegate.py` and `react/tools/memory_intelligence.py` to execute through runtime modules (`agent.get_runtime_module`) with delegate budget/depth guards and typed output coercion.
  **Outcome:** More predictable delegate behavior with lower recursion overhead and safer response shaping.
- **Change:** Updated `core/llm_tools.py` query paths to coerce non-string LM returns to strings and normalize async result indexing.
  **Outcome:** Callers now receive consistently typed `str` outputs from LLM query helpers.
- **Change:** Removed `NotificationCenter` usage from the top-header shell layout and aligned related frontend tests/mocks.
  **Outcome:** Header surface is simplified and ReactFlow test coverage no longer emits console-error noise from incomplete mocks.
- **Change:** Replaced `PromptInput` usage in `SkillCreationFlow` with the new `ChatInput` surface and threaded `traceEnabled` / attachment metadata through submit handlers.
  **Outcome:** Frontend runtime can toggle tracing per message and stage attachment metadata without changing the current backend message contract.
- **Change:** Updated WS chat store payload construction to support per-message trace override (`trace_mode: "compact"` vs `"off"`), with matching store tests.
  **Outcome:** Trace emission behavior now aligns directly with composer-level "Think" intent.
- **Change:** Updated docs (`AGENTS.md`, `docs/reference/source-layout.md`, `docs/reference/code-health-map.md`) to clarify core host-vs-sandbox boundaries and utility module ownership.
  **Outcome:** Lower documentation drift and clearer module-level onboarding guidance.
- **Change:** Updated `pyproject.toml` dependency constraints to align base, `server`, and `full` extras for `asyncpg`, `sqlalchemy`, `greenlet`, `psycopg`, and `uvicorn` (with explicit `<major>` upper bounds), and refreshed `uv.lock`.
  **Outcome:** More consistent dependency resolution across installation modes and lower risk of accidental cross-environment drift.

### Fixed

- **Change:** Added compatibility shim behavior in `fleet_rlm.utils.tools` while re-exporting modal helpers and `regex_extract` from canonical modules.
  **Outcome:** Legacy `fleet_rlm.utils.tools` imports continue working during the transition to `fleet_rlm.utils.regex` and `fleet_rlm.utils.modal`.
- **Change:** Synchronized `src/fleet_rlm/__init__.py::__version__` with project metadata in `pyproject.toml` (`0.4.95`).
  **Outcome:** Package runtime version introspection now matches build metadata and lockfile state.

### Removed

- **Change:** Removed the deprecated `fleet_rlm.stateful` module tree and associated unit tests.
  **Outcome:** Reduced dead-code maintenance burden and eliminated obsolete stateful sandbox abstractions from the package.
- **Change:** Removed unused server service package stub and request-scoped `get_react_agent` dependency provider from `server/deps.py`.
  **Outcome:** Leaner server dependency surface with less legacy wiring.

### Merged Pull Requests

- [#96](https://github.com/Qredence/fleet-rlm/pull/96): Release v0.4.95 preparation, runtime/tooling cleanup, and frontend/chat UX refinements.

## [0.4.94] - 2026-03-03

### Highlights (User Impact)

- Completed the WS-first migration by removing remaining deprecated HTTP chat compatibility surfaces.
- Improved execution tracing maintainability and readability across streaming, step-building, and websocket chat orchestration.
- Packaging now guarantees published wheels bundle synchronized frontend assets, so `fleet web` installs include the latest UI.

### Added

- **Change:** Added `execution-canvas-smoke.spec.ts` end-to-end coverage for graph lane labels, elapsed timing, and full-output rendering.
  **Outcome:** Reduced regression risk for Artifact Canvas readability and payload truncation behavior in browser flows.
- **Change:** Added release artifact integrity check script `scripts/check_wheel_frontend_sync.py` and wired it into release workflows.
  **Outcome:** Release builds now verify wheel UI payload correctness and prevent unintended frontend package leakage.

### Changed

- **Change:** Refactored websocket chat internals into smaller runtime/session helpers in `ws/api.py` without changing endpoint contracts.
  **Outcome:** Lower complexity in the main chat handler and safer maintenance of session lifecycle/persistence flow.
- **Change:** Refactored execution step and citation normalization helpers (`step_builder.py`, `streaming_citations.py`, `streaming.py`) for clearer event construction paths.
  **Outcome:** Better long-term maintainability for execution timeline/event shaping with preserved runtime behavior.
- **Change:** Updated backend/frontend docs and API contract tests to align with current WS-first routes and generated OpenAPI surfaces.
  **Outcome:** Less documentation drift and clearer integration expectations for client consumers.
- **Change:** `uv build` now runs frontend bundling in release/source builds, and local source `fleet web` now prefers `src/frontend/dist` when available.
  **Outcome:** End users get up-to-date packaged UI assets and contributors see latest local frontend output without manual asset sync.

### Fixed

- **Change:** Mounted frontend `branding/` static assets in FastAPI alongside `/assets` for Web UI serving.
  **Outcome:** Brand/logo assets (for example `/branding/logo-mark.svg`) now load correctly instead of falling through to SPA HTML fallback.

### Removed

- **Change:** Removed deprecated HTTP chat router and legacy frontend `rlm-api` compatibility exports/types (`/api/v1/chat`, `rlmCoreEndpoints` surface).
  **Outcome:** Smaller API/client surface area and reduced risk of accidental dependence on removed legacy paths.

### Merged Pull Requests

- [#94](https://github.com/Qredence/fleet-rlm/pull/94): Remove deprecated HTTP chat compatibility and related cleanup.
- [#95](https://github.com/Qredence/fleet-rlm/pull/95): Filesystem UI and follow-up fixes across frontend/backend surfaces.

## [0.4.93] - 2026-03-03

### Highlights (User Impact)

- Improved ReAct responsiveness with async-first DSPy delegation and realtime reasoning stream fixes.
- Removed deprecated legacy API surfaces and compatibility layers to reduce contract drift and maintenance overhead.
- Hardened config/env parsing and made server imports lazier so config modules can be imported without requiring FastAPI/uvicorn.

### Changed

- **Change:** Migrated ReAct delegation/streaming paths to async-first DSPy flow and updated frontend reasoning event adapters.
  **Outcome:** More reliable realtime reasoning updates and smoother long-running interactive sessions.
- **Change:** Consolidated on canonical backend/frontend API paths and removed obsolete legacy routes/modules.
  **Outcome:** Smaller supported surface area with clearer runtime contracts and less accidental integration with deprecated endpoints.
- **Change:** Centralized duplicated environment parsing helpers and reused them across analytics/core/server config modules.
  **Outcome:** Lower config drift and easier long-term maintenance.

### Fixed

- **Change:** Resolved reasoning-stream consistency issues in the realtime event pipeline.
  **Outcome:** Reasoning output is delivered more consistently in chat/streaming clients.
- **Change:** Made `fleet_rlm.server` imports lazy for config-only consumers.
  **Outcome:** Importing runtime config no longer hard-requires FastAPI/uvicorn in non-server contexts.
- **Change:** Aligned `fleet_rlm.__version__` with project metadata in `pyproject.toml`.
  **Outcome:** Restored release metadata consistency and unblocked release/UI schema CI checks.

### Merged Pull Requests

- [#90](https://github.com/Qredence/fleet-rlm/pull/90): Remove deprecated legacy API surfaces and clean up unused paths.
- [#91](https://github.com/Qredence/fleet-rlm/pull/91): Refactor env parsing helpers and harden config imports.
- [#92](https://github.com/Qredence/fleet-rlm/pull/92): Async-first DSPy delegation + realtime reasoning stream fixes.

## [0.4.92] - 2026-02-28

### Changed

- **Change:** Aligned package metadata/version exports and hardened root lazy-export surface in `fleet_rlm.__init__`.
  **Outcome:** `__version__` now matches project metadata and static analysis tooling can resolve package exports without changing lazy import behavior.
- **Change:** Modernized FastAPI server contracts for `fastapi>=0.134.0,<1` by introducing reusable `Annotated` dependency aliases and explicit route return/response models.
  **Outcome:** Cleaner dependency injection, tighter OpenAPI fidelity (including `/api/v1/chat` and `/api/v1/auth/*`), and improved generated frontend typing.
- **Change:** Marked legacy SQLite compatibility CRUD endpoints (`/api/v1/tasks*`, `/api/v1/sessions*` except `/api/v1/sessions/state`) as deprecated in API schema and documented v0.5.0 removal intent.
  **Outcome:** Existing behavior remains intact while making migration and cleanup policy explicit for the next release cycle.

## [0.4.9] - 2026-02-27

### Changed

- **Change:** Upgraded core development and runtime dependencies: fastapi[standard] to >=0.133.1, websockets to >=16, ruff to >=0.15.4, ty to >=0.0.19.
  **Outcome:** All lint, type-check, and unit test gates pass with upgraded versions; fully backward compatible with no code changes required.
- **Change:** Implemented Wave 7.1 structural simplification with canonical package regrouping and one-release compatibility shims (`server/routers/ws/`, `server/execution/`, `react/tools/`, `terminal/chat.py`, `server/runtime_settings.py`, `react/signatures.py`, `models/streaming.py`).
  **Outcome:** Cleaner module ownership and reduced namespace ambiguity without intentional HTTP/WS/frontend contract drift; legacy import paths remain supported through `v0.5.0`.
- **Change:** Completed refactor cleanup phases for server/runtime surfaces, including app/request-bound server state lifecycle, legacy SQLite isolation, and WebSocket streaming loop decomposition.
  **Outcome:** Lower runtime ambiguity, improved testability, and reduced complexity in `server` and websocket internals.
- **Change:** Consolidated duplicate server schemas and removed compatibility shim modules/routes (`server/dependencies.py`, flat `server/schemas.py`, router shim files), with planned stub routes now returning explicit `501`.
  **Outcome:** Clearer canonical imports/surfaces and more truthful API behavior for unimplemented endpoints.
- **Change:** Executed Wave 7 server-first simplification with contract lock: added API/WS/frontend contract tests and decomposed websocket/session/execution internals into focused modules (`ws_message_loop`, `ws_turn`, `ws_repl_hook`, `ws_session_store`, execution event sanitizer/step builder facade split).
  **Outcome:** Lower nesting/ownership ambiguity in hot paths while preserving `/api/v1/*` routes, websocket endpoints, and envelope contracts.
- **Change:** Refactored React/stateful internals for maintainability with no intentional external behavior changes: extracted streaming citation assembly (`react/streaming_citations.py`), normalized tool-builder contexts, deduped chat result shaping, and centralized stateful result/workspace adapters.
  **Outcome:** Reduced duplicated logic and clearer module boundaries with frontend/backend wiring preserved (`/api/v1/ws/chat`, `/api/v1/ws/execution`, runtime endpoints, and frontend URL derivation semantics).

## [0.4.8] - 2026-02-24

### Highlights (User Impact)

- Completed milestone `v0.4.8` end-to-end across Phase 0-4: foundation hardening, DB/schema enablers, Canvas UX delivery, telemetry propagation, and live integration validation.
- Upgraded the browser chat and artifact surfaces with AI Elements rendering, typed timeline/preview summaries, and richer graph diagnostics.
- Shipped runtime/privacy stabilization: simplified settings, LM runtime wiring, anonymous telemetry defaults, and UI-to-backend telemetry preference enforcement.

### Added

- **Change:** Added Codex multi-agent delivery bootstrap and phase runbook system for milestone execution (`QRE-321`).
  **Outcome:** Repeatable, documented phase-by-phase delivery workflow with stronger execution hygiene.
- **Change:** Added deterministic RLM mock trajectory assessment coverage and live integration validation harness/test (`QRE-300`, `QRE-301`).
  **Outcome:** Both fast deterministic regression confidence and credential-gated end-to-end tracing validation are now available.
- **Change:** Added RLM/DSPy + Modal infrastructure persistence schema (`QRE-312`, `QRE-313`) and Neon optimization pass (`QRE-314`).
  **Outcome:** Better trace/program persistence and improved Postgres performance characteristics for Neon deployments.
- **Change:** Added Canvas graph/timeline/preview feature set: tool badges, REPL code preview, failed-node error details, trajectory TAO chain view, edge elapsed labels, typed timeline summaries, typed final-output rendering (`QRE-302`, `QRE-304`, `QRE-305`, `QRE-306`, `QRE-307`, `QRE-309`, `QRE-310`).
  **Outcome:** Artifact analysis is substantially more readable and actionable in the UI.
- **Change:** Added AI Elements chat QA + renderer hardening artifacts, including deterministic `/__dev/chat-elements` route and renderer coverage (`QRE-322`).
  **Outcome:** Stable visual QA path and stronger regression protection for chat rendering behavior.

### Changed

- **Change:** Refactored backend architecture boundaries and runtime/demo separation (`QRE-296`, `QRE-297`, `QRE-298`, `QRE-299`).
  **Outcome:** Cleaner dependency boundaries, reduced demo leakage risk, and simpler router organization.
- **Change:** Simplified settings surface to only functional settings and wired LM integration fields to runtime APIs (`QRE-318`, `QRE-319`).
  **Outcome:** More trustworthy settings UX with real runtime-backed behavior.
- **Change:** Standardized telemetry defaults and instrumentation behavior for anonymous-first operation (`QRE-316`, `QRE-317`).
  **Outcome:** Safer telemetry posture with reduced PII risk.

### Fixed

- **Change:** Fixed reasoning/thinking stream formatting and chunk coalescing issues in AI Elements chat rendering (`QRE-322`).
  **Outcome:** Reasoning panels render coherent content instead of fragmented one-word lines.
- **Change:** Implemented end-to-end telemetry preference propagation from UI toggle to backend AI analytics callback (`QRE-320`).
  **Outcome:** Disabling telemetry in UI now suppresses backend AI analytics emission for that session flow.

### Merged Pull Requests

- [#74](https://github.com/Qredence/fleet-rlm/pull/74): v0.4.8 Phase 0 bootstrap (`QRE-321`).
- [#75](https://github.com/Qredence/fleet-rlm/pull/75): v0.4.8 Phase 1 foundation (`QRE-296`, `QRE-297`, `QRE-299`, `QRE-300`, `QRE-311`, `QRE-316`, `QRE-318`).
- [#76](https://github.com/Qredence/fleet-rlm/pull/76): v0.4.8 Phase 2 feature enablers (`QRE-298`, `QRE-302`, `QRE-312`, `QRE-313`, `QRE-317`, `QRE-319`).
- [#77](https://github.com/Qredence/fleet-rlm/pull/77): v0.4.8 Phase 3 feature delivery (`QRE-304`, `QRE-305`, `QRE-306`, `QRE-307`, `QRE-309`, `QRE-310`, `QRE-314`, `QRE-320`).
- [#78](https://github.com/Qredence/fleet-rlm/pull/78): v0.4.8 Phase 4 integration validation (`QRE-301`) and chat QA follow-up integration (`QRE-322`).

### Implemented Milestone Issues

`QRE-296`, `QRE-297`, `QRE-298`, `QRE-299`, `QRE-300`, `QRE-301`, `QRE-302`, `QRE-304`, `QRE-305`, `QRE-306`, `QRE-307`, `QRE-309`, `QRE-310`, `QRE-311`, `QRE-312`, `QRE-313`, `QRE-314`, `QRE-316`, `QRE-317`, `QRE-318`, `QRE-319`, `QRE-320`, `QRE-321`, `QRE-322`.

## [0.4.7] - 2026-02-22

### Highlights (User Impact)

- Added runtime settings + connectivity diagnostics surfaces so local operators can configure and validate LM/Modal integrations from the app workflow.
- Hardened WebSocket chat/execution handling to reduce local-session regressions and improve execution event stream stability.
- Continued Web UI-first docs alignment and frontend/runtime integration cleanup for a smoother browser-based workflow.

### Added

- **Change:** Added backend runtime settings + diagnostics endpoints under `/api/v1/runtime/*` and supporting runtime settings infrastructure.
  **Outcome:** Local developers can inspect and validate runtime configuration (LM/Modal connectivity) through a dedicated API/UI workflow instead of ad-hoc environment debugging.
- **Change:** Added frontend runtime settings UI integration and runtime health warning surfacing in the skill creation flow.
  **Outcome:** The primary Web UI provides clearer runtime setup guidance and feedback while working in-browser.
- **Change:** Added/refined test coverage around runtime settings, WebSocket routing/helpers, and trajectory payload handling in the changed surfaces.
  **Outcome:** Lower regression risk for the Web UI + server integration paths.

### Changed

- **Change:** Updated docs and developer-facing guidance (`README.md`, `AGENTS.md`, docs indexes/guides) to align with runtime settings, WebSocket behavior, and the Web UI-first workflow.
  **Outcome:** Reduced documentation drift and clearer onboarding for local/browser-based usage.
- **Change:** Normalized/cleaned frontend and backend integration surfaces (including trajectory payload and legacy bridge cleanup) as part of the frontend/runtime alignment work.
  **Outcome:** Simpler maintenance of the current Web UI stack and less confusion around deprecated bridge paths.
- **Change:** Performed maintenance-only code quality cleanup (CodeQL/unused variable/readability refactors) in TUI scripts/helpers via PR #67.
  **Outcome:** Improved code readability and static-analysis hygiene without intended user-facing behavior changes.

### Fixed

- **Change:** Hardened `/api/v1/ws/chat` and `/api/v1/ws/execution` behavior, including regressions around local persistence wrapper flow and bounded internal step emission.
  **Outcome:** More reliable interactive chat and execution-event streaming during local web sessions.
- **Change:** Cleaned up pytest discovery collisions for local debug scripts (`test_*.py` renamed to `debug_*.py`).
  **Outcome:** Lower risk of accidental test collection noise in local and CI test runs.

### Merged Pull Requests

- [#67](https://github.com/Qredence/fleet-rlm/pull/67): Refactor/code-quality cleanup for readability and static-analysis findings.
- [#72](https://github.com/Qredence/fleet-rlm/pull/72): Frontend runtime settings, websocket hardening, and docs sync.

## [0.4.6] - 2026-02-19

### Highlights (User Impact)

- Promoted the React Web UI to the primary interface for `fleet-rlm`, with backend-served frontend assets and end-to-end realtime chat/execution streaming.
- Introduced a dedicated `/ws/execution` channel for structured execution lifecycle events while preserving `/ws/chat` compatibility for existing clients.
- Improved terminal operator productivity with centralized keyboard shortcuts, better pane focus handling, and responsive layout behavior.
- Tightened WebSocket auth/runtime behavior and aligned docs so deployment expectations are clearer and safer.
- Aligned Neon data model docs/migrations/runtime guidance to reduce onboarding drift and improve operator setup consistency.

### Added

- **Change:** Added integrated frontend packaging/serving flow so built React assets are available directly from the backend runtime.
  **Outcome:** Users can run `uv run fleet web` for a browser-first experience without separate frontend hosting.
- **Change:** Added execution-event infrastructure and `/ws/execution` subscription support for structured lifecycle updates (`execution_started`, `execution_step`, `execution_completed`).
  **Outcome:** Artifact-canvas and observability consumers can track runs without parsing mixed chat traffic.
- **Change:** Added and documented NeonDB migration/bootstrap workflows across schema, scripts, and setup docs.
  **Outcome:** Teams have a repeatable path for provisioning and validating multi-tenant runtime persistence.

### Changed

- **Change:** Refined TUI interaction patterns with shared keyboard shortcut/focus handling plus improved input ergonomics.
  **Outcome:** Faster keyboard-driven operation and more predictable behavior across chat, sidebar, and input panes.
- **Change:** Updated WebSocket auth flow wiring and synchronized runtime docs (`README`, auth/API guides, contributor docs).
  **Outcome:** Lower auth configuration ambiguity between development and deployment environments.
- **Change:** Realigned server/runtime docs and migration references around the current Neon data model.
  **Outcome:** Reduced documentation drift between implementation and operator guidance.

### Fixed

- **Change:** Resolved WebSocket auth flow inconsistencies and related runtime expectation mismatches.
  **Outcome:** More reliable authenticated WebSocket sessions across clients.
- **Change:** Improved run-completion/event-sequencing coverage for WebSocket flows.
  **Outcome:** Lower regression risk for event ordering during execution streaming.

### Merged Pull Requests

- [#43](https://github.com/Qredence/fleet-rlm/pull/43): Add execution event streaming endpoints.
- [#44](https://github.com/Qredence/fleet-rlm/pull/44): Align Neon data model docs and migrations.
- [#46](https://github.com/Qredence/fleet-rlm/pull/46): Enhance chat/input UX with keyboard shortcuts and interaction improvements.
- [#47](https://github.com/Qredence/fleet-rlm/pull/47): Fix WebSocket auth flows and update docs.

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
