# Phase 0 Gap Matrix

This matrix starts Phase 0 by reconciling the cleanup plan with the current codebase and defining what should be removed, adapted, or deferred before source cleanup begins.

## Authority Model

- **Product direction:** `plan/PLANS.md`, `plan/PLAN-MATRIX.md`, `plan/PLAN-DIAGRAMS.md`, and `plan/PLAN-INFO.md` define the target architecture.
- **Implementation boundaries:** `AGENTS.md`, `src/fleet_rlm/AGENTS.md`, and `docs/agent-harness/architecture-invariants.md` define the current repo constraints that should not be broken casually.
- **Tests:** tests are validation signals only. They are not source of truth and should be updated or removed when they encode obsolete behavior.
- **Generated artifacts:** generated OpenAPI/frontend/UI build outputs remain out of scope for hand edits.

## Phase 0 Task Matrix

| Task | Plan intent | Current evidence | Decision | Implementation note |
| --- | --- | --- | --- | --- |
| `0.1` Remove unused chunking strategies | Keep only `chunk_by_size` and `chunk_by_headers`; remove timestamp and JSON-key chunkers. | `chunk_by_timestamps` and `chunk_by_json_keys` were exposed through host content helpers, runtime tool/module selection, and sandbox assets. | **Completed** | Timestamp and JSON-key chunkers were removed from host content helpers, runtime/module selection, sandbox assets, exports, CLI usage text, and obsolete validation tests. |
| `0.2` Simplify document ingestion | MarkItDown primary; PDF fallback through `pypdf`; raw text fallback otherwise; remove EPUB/PPT-specific paths. | `runtime/content/ingestion.py` already followed the primary/fallback shape, while `MARKITDOWN_SUFFIXES` and URL suffix handling still advertised Office, EPUB, spreadsheet, and RTF support. | **Completed** | Supported MarkItDown/download suffix declarations were narrowed to PDF, HTML, and text-like formats while preserving PDF fallback and raw text fallback. |
| `0.3` Deactivate speculative tools | Remove `memory_tools` and `buffer_tools` from registry until rebuilt around the persistent volume. | `runtime/tools/registry.py` imported placeholder modules that raised unless rebound. Bound memory/buffer behavior also exists in `runtime/tools/binding.py`, CLI aliases, and sandbox execution helpers. | **Completed** | Placeholder `memory_tools` and `buffer_tools` were removed from `TOOL_MODULE_NAMES`; bound runtime/CLI/sandbox helpers were preserved. |
| `0.4` Prune configuration | Reduce config to `llm`, `sandbox`, `volume`, and `rlm_settings`; move advanced/unused keys to `config_full.yaml`. | `config.yaml` had `api_keys`, `database`, `memory`, and `analytics`; `env.py` defaults still materialize omitted sections. | **Completed** | `config_full.yaml` preserves the full current config, and default `config.yaml` now keeps only `llm`, `sandbox`, `volumes`, and `rlm_settings`. |
| `0.5` Delete unused preview module | Remove `runtime/content/preview.py`. | `head_tail_preview` was consumed by streaming event shaping and delegation telemetry. | **Completed** | `head_tail_preview` moved to `fleet_rlm.utils.preview`; `runtime/content/preview.py` was deleted. |
| `0.6` Thin interpreter protocol | Remove `git_config` and `secrets` from `RLMInterpreterProtocol`; keep `volume_mount_path`, `timeout`, and `env_vars`. | Current protocol already lacks `git_config` and `secrets`. | **Verified complete** | No protocol edit required. |
| `0.7` Remove dual-mode routing | Strip external simple/complex routing; all traffic should go to sandbox agent. | Current backend docs state the public runtime is Daytona-only. Websocket payload has `execution_mode` as a Daytona-backed per-turn hint, not public runtime selection. | **Verified complete** | No public request-side `runtime_mode` selection was found; `execution_mode` is preserved. |
| `0.8` Verify no regressions | Run delegation/tool tests. | Tests are not authoritative, but focused checks are useful to catch accidental breakage after intended cleanup. | **Validation signal** | Use format/lint/typecheck and focused runtime checks after implementation. Update obsolete tests to match Phase 0 decisions. |

## Recommended Implementation Order

1. **Registry deactivation slice** — completed
   - Remove `buffer_tools` and `memory_tools` from `TOOL_MODULE_NAMES`.
   - Preserve bound tools in `runtime/tools/binding.py` and CLI aliases unless separately removed.
   - Update discovery expectations that assumed placeholder registry modules.

2. **Ingestion support narrowing slice** — completed
   - Reduce advertised MarkItDown suffixes to the actual Phase 0 essentials.
   - Keep PDF MarkItDown -> `pypdf` fallback and raw text fallback.
   - Update URL suffix/content-type handling to match the narrowed scope.

3. **Chunking strategy reduction slice** — completed
   - Remove timestamp/JSON strategy selection from `chunk_document` and grounded-answer modules.
   - Remove exports and implementation from `runtime/content/chunking.py` after sandbox asset compatibility is resolved.
   - Update or remove obsolete tests.

4. **Config reference/prune slice** — completed
   - Copy the current full config into `config_full.yaml` before pruning.
   - Prune default `config.yaml` to `llm`, `sandbox`, `volumes`, and `rlm_settings`.
   - Preserve omitted sections through Pydantic defaults and environment-derived runtime settings.

## First Implementation Candidate

Phase 0 implementation is complete. Continue with Phase 1 only after reviewing the persistent Daytona volume/session architecture targets.

## Validation Lane

- **Static baseline:** `make format-check`, `make lint`, `make typecheck`.
- **Focused signal checks:** runtime tool discovery/binding checks after updating obsolete assertions.
- **Escalation:** Daytona-focused tests only if delegation, interpreter, or sandbox lifecycle code changes.
