# Phase 3 Progress Tracker

## Scope

Phase 3 builds the essential tool set for reasoning, coding, web research, document analysis, persistent knowledge, memory, and skills while preserving the existing Daytona-backed runtime contract.

## Phase 2 Prerequisite Check

- **Escalating agent module:** `EscalatingFleetModule` exists with ChainOfThought-to-RLM escalation and focused unit coverage.
- **Runtime wiring status:** `AgentRuntime` still defaults to `use_escalation=False`, so Phase 2 is available as an opt-in path rather than the default runtime path.
- **Decision for Phase 3:** Keep Phase 3 focused on essential tool availability and track Phase 2 default wiring separately unless product scope changes.

## Completed

- **Memory tools:** `remember` and `recall` exist as volume-backed SQLite tools with root-agent write gating.
- **Memory DB initialization:** Daytona volume layout initialization creates `memories/core.db` with the minimal memory schema.
- **Existing coding and execution tools:** Sandbox filesystem, command/code execution, patch/edit-adjacent tools, and delegation tools remain registered through the runtime tool registry.
- **Tool registry expansion:** `web_search`, `fetch_page`, `search_knowledge`, and `load_skill` are registered runtime tools.
- **Knowledge persistence:** Bound `load_document` writes extracted text to `knowledge/ingested/` and updates `knowledge/index.json` when a volume mount is available.
- **Skill loading:** `load_skill` reads human-curated markdown skills from the user scope before falling back to the system scope.
- **Focused validation:** Unit coverage now exercises Phase 3 registry exposure, knowledge persistence/search, skill loading, bound volume tools, and web tool behavior.
- **Pydantic schemas:** All Phase 3 tools now use typed Pydantic models for input validation and output serialization, improving type safety and documentation.

## In Progress

_None — initial Phase 3 tool-set slice is complete._

## Pending

- **Phase 2 default wiring:** Decide whether `use_escalation=True` should become the default runtime path.
- **Search provider configuration:** `web_search` uses environment-provided API keys and should be validated with a live provider before Phase 3 closeout.
- **Optional parsing dependencies:** `readability-lxml`, `beautifulsoup4`, `lxml`, `pdfplumber`, and `python-docx` remain dependency candidates; Phase 3 starts with stdlib/installed dependency behavior unless richer extraction becomes required.
- **Live Daytona verification:** Run an end-to-end Daytona session that loads a document, persists knowledge, searches it, loads a skill, and uses memory tools.

## Validation Log

- `uv run pytest -q tests/unit/runtime/test_escalating_module.py tests/unit/runtime/test_volume_memory_tools.py tests/unit/runtime/test_tools.py tests/unit/runtime/test_phase3_tools.py` — pass (39/39).
- `make format-check` — pass after formatting new Phase 3 files.
- `make lint` — pass.
- `make typecheck` — pass.
- Pydantic migration added `src/fleet_rlm/runtime/tools/schemas.py` with typed input/output models for all Phase 3 tools; updated tool implementations, binding, and tests; validation passed after reformat and import sorting.
