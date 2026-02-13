# fleet-rlm: AI coding instructions

## Start here (architecture map)

- Follow the runtime pipeline: `src/fleet_rlm/cli.py` → `src/fleet_rlm/runners.py` → `dspy.RLM` signatures in `src/fleet_rlm/signatures.py` → `ModalInterpreter` in `src/fleet_rlm/core/interpreter.py` → sandbox JSON driver in `src/fleet_rlm/core/driver.py`.
- Keep ReAct orchestration in `src/fleet_rlm/react/agent.py` and tool behavior in `src/fleet_rlm/react/tools.py`; avoid mixing tool logic into routers/CLI.
- Service surfaces are optional wrappers over the same core flows: FastAPI in `src/fleet_rlm/server/` and FastMCP in `src/fleet_rlm/mcp/server.py`.

## Required developer workflows

- Use `uv` for Python workflows and dependency sync.
- Install by surface area:
  - core/dev: `uv sync --extra dev`
  - API server: `uv sync --extra dev --extra server`
  - MCP server: `uv sync --extra dev --extra mcp`
- Quality gates used in this repo:
  - tests: `uv run pytest`
  - lint: `uv run ruff check src tests`
  - format: `uv run ruff format src tests`
  - types: `uv run ty check src`
- Interactive runtime: OpenTUI is the only supported chat UI (`uv run fleet-rlm code-chat --opentui`), with backend on `uv run fleet-rlm serve-api`.

## Project-specific coding conventions

- Keep Python compatibility at 3.10+ and prefer explicit type hints.
- Use `ty` (not mypy) for type checking.
- ReAct tools should be closure-based functions returning `dict[str, Any]` (see `build_tool_list` in `src/fleet_rlm/react/tools.py`).
- For sandbox completion, use `SUBMIT(...)` or set `Final` in executed code; the driver handles both (`src/fleet_rlm/core/driver.py`).
- Respect execution profiles (`ROOT_INTERLOCUTOR`, `RLM_DELEGATE`, `MAINTENANCE`) when exposing helper/tool access (`src/fleet_rlm/core/interpreter.py`).
- `chunk_by_headers` chunks use keys `header` + `content` (not `body`) across host/sandbox implementations.

## Document and long-context handling

- Do not read PDFs/binary docs with raw `Path.read_text()`.
- Use the shared ingestion path in `src/fleet_rlm/react/tools.py` (`_read_document_content`): MarkItDown first, then pypdf fallback for PDFs, with OCR guidance for scanned PDFs.
- Keep long-context helpers (`peek`, `grep`, chunking, buffers, volume helpers) aligned between `src/fleet_rlm/core/driver.py` and `src/fleet_rlm/chunking/`.

## Stateful server/session contracts

- `/ws/chat` is the primary interactive endpoint (`src/fleet_rlm/server/routers/ws.py`).
- WebSocket payloads should carry identity envelope fields: `workspace_id`, `user_id`, `session_id` (see `src/fleet_rlm/server/schemas.py`).
- Session manifests persist under `workspaces/<workspace_id>/users/<user_id>/...` paths on Modal Volume.
- Do not add `from __future__ import annotations` to `src/fleet_rlm/server/routers/ws.py` (FastAPI WebSocket type detection depends on runtime annotations there).

## Environment/config expectations

- Planner LM config comes from `.env` via `src/fleet_rlm/core/config.py` (`DSPY_LM_MODEL` + `DSPY_LLM_API_KEY`/`DSPY_LM_API_KEY`; optional `DSPY_LM_API_BASE`, `DSPY_LM_MAX_TOKENS`).
- Modal auth/secrets are first-class runtime dependencies; prefer `LITELLM` secret naming unless intentionally changing infra conventions.
