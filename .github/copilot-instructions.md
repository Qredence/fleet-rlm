# fleet-rlm: AI coding instructions

## Start here (architecture map)

- Follow the runtime path: `src/fleet_rlm/cli.py` → `src/fleet_rlm/runners.py` → signatures in `src/fleet_rlm/signatures.py` → `ModalInterpreter` in `src/fleet_rlm/core/interpreter.py` → sandbox JSON driver in `src/fleet_rlm/core/driver.py`.
- Keep orchestration boundaries: `src/fleet_rlm/react/agent.py` owns ReAct/session state, `src/fleet_rlm/react/tools*.py` owns tool behavior, and `src/fleet_rlm/react/commands.py` maps WebSocket commands to tools.
- FastAPI (`src/fleet_rlm/server/`) and FastMCP (`src/fleet_rlm/mcp/server.py`) are thin service wrappers over the same runner/agent flows.

## Required developer workflows

- Use `uv` for env/dependencies (`uv sync --extra dev`, plus `--extra server` or `--extra mcp` per surface).
- Primary quality gate in this repo: `uv run ruff check src tests && uv run ty check src && uv run pytest -q`.
- Use `ty` for types (not mypy), `ruff` for lint/format, and `pytest` for tests.
- Use Make shortcuts when useful: `make sync-scaffold`, `make precommit-install`, `make precommit-run`, `make release-check`.
- Before debugging type/lint failures, clear stale caches (`.ruff_cache`, `__pycache__`, `.pytest_cache`, `.mypy_cache`) and run `pre-commit clean`.
- Interactive runtime is OpenTUI only: `code-chat` expects Bun and a running backend (`serve-api`).

## Project-specific conventions

- Python target is 3.10+ with explicit type hints.
- ReAct tools are closure-based functions returning `dict[str, Any]`, then wrapped as `dspy.Tool` (see `build_tool_list` in `src/fleet_rlm/react/tools.py`).
- Sandbox completion supports both `SUBMIT(...)` and `Final = ...` (`src/fleet_rlm/core/driver.py`).
- Respect execution profiles (`ROOT_INTERLOCUTOR`, `RLM_DELEGATE`, `MAINTENANCE`); WebSocket chat defaults to root/interlocutor and command execution temporarily uses delegate profile.
- Keep chunk contracts stable: `chunk_by_headers` returns `header` + `content` (not `body`) in both `src/fleet_rlm/chunking/headers.py` and sandbox driver helpers.
- Do not read PDFs/binary docs with raw `Path.read_text()`; route document ingestion through `_read_document_content` (MarkItDown first, then pypdf fallback, OCR guidance for scanned PDFs).
- Keep helper behavior aligned between host chunking (`src/fleet_rlm/chunking/`) and sandbox helpers in `src/fleet_rlm/core/driver.py`.

## Stateful server/session contracts

- `/ws/chat` is the primary interactive endpoint (`src/fleet_rlm/server/routers/ws.py`).
- WS payload identity envelope should include `workspace_id`, `user_id`, `session_id` (`src/fleet_rlm/server/schemas.py`).
- Session keys are `workspace_id:user_id`; persisted manifests live at `workspaces/<workspace_id>/users/<user_id>/memory/react-session.json` on the Modal volume.
- Use `/sessions/state` for server-side session introspection.
- Do **not** add `from __future__ import annotations` to `src/fleet_rlm/server/routers/ws.py` (breaks FastAPI WebSocket parameter inspection).

## Environment and runtime expectations

- Planner LM comes from `.env` via `src/fleet_rlm/core/config.py`: `DSPY_LM_MODEL` + (`DSPY_LLM_API_KEY` or `DSPY_LM_API_KEY`), optional `DSPY_LM_API_BASE`, `DSPY_LM_MAX_TOKENS`.
- Modal secret naming defaults to `LITELLM`; keep this unless intentionally changing infra conventions.
- `serve-api` defaults to persistent volume `rlm-volume-dspy` when `interpreter.volume_name` is unset.
- For Modal sandbox work, verify volume availability and credentials (`modal setup` + volume/secret readiness) before running tests.
