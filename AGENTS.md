# AGENTS.md

Guidance for contributors working in this repository.

## Project Overview

`fleet-rlm` is a Python package for Recursive Language Models (RLM) using DSPy + Modal for secure long-context code execution.

Reference: <https://arxiv.org/abs/2501.123>

## Setup

```bash
# from repo root
uv sync --extra dev
uv sync --extra dev --extra server
uv sync --extra dev --extra mcp
cp .env.example .env
```

## Modal Setup

```bash
# from repo root
uv run modal setup
uv run modal volume create rlm-volume-dspy
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=... \
  DSPY_LM_API_BASE=... \
  DSPY_LLM_API_KEY=...

# serve-api defaults to persistent volume `rlm-volume-dspy`
# when interpreter.volume_name is not explicitly provided.
```

## Common Commands

```bash
# from repo root
uv run fleet-rlm --help
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"
uv run fleet-rlm run-architecture --docs-path rlm_content/dspy-knowledge/dspy-doc.txt --query "Extract all modules and optimizers"
uv run fleet-rlm code-chat --opentui
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm serve-api interpreter.volume_name=my-volume --port 8000
uv run fleet-rlm serve-mcp --transport stdio

# Quality gate (run all three before pushing)
uv run ruff check src tests && uv run ty check src && uv run pytest -q

# Individual checks
uv run ruff check src tests
uv run ruff format src tests
uv run ty check src
uv run pytest
```

## Interactive Surface

- OpenTUI under `tui/` is the active and only supported interactive runtime.
- Python Textual and legacy prompt-toolkit UI runtimes have been removed (v0.4.0).
- `src/fleet_rlm/interactive/models.py` is retained — it contains streaming data models (`StreamEvent`, `TurnState`) used by `react/streaming.py`, not UI code.

## Architecture Highlights

- `src/fleet_rlm/config.py`: top-level Hydra `AppConfig` loader and runtime settings
- `src/fleet_rlm/conf/`: Hydra config YAML directory
- `src/fleet_rlm/core/config.py`: env loading + planner LM configuration
- `src/fleet_rlm/core/interpreter.py`: `ModalInterpreter` lifecycle + JSON bridge + execution profiles (`ROOT_INTERLOCUTOR`, `RLM_DELEGATE`, `MAINTENANCE`)
- `src/fleet_rlm/core/driver.py`: sandbox-side execution driver, profile-aware helper/tool gating, and Final/SUBMIT extraction
- `src/fleet_rlm/logging.py`: structured logging helper
- `src/fleet_rlm/react/agent.py`: `RLMReActChatAgent` (`dspy.Module` subclass)
- `src/fleet_rlm/react/tools.py`: ReAct tool definitions (wrapped with `dspy.Tool`)
- `src/fleet_rlm/react/tools_sandbox.py`: sandbox-specific tools (`rlm_query`, `edit_file`) with depth enforcement
- `src/fleet_rlm/react/streaming.py`: async/streaming ReAct execution with trajectory normalization
- `src/fleet_rlm/react/commands.py`: WebSocket command dispatch → tool mapping
- `src/fleet_rlm/runners.py`: high-level task runners
- `src/fleet_rlm/cli.py`: Typer CLI entrypoint
- `src/fleet_rlm/server/`: optional FastAPI server (`/ws/chat`, `/chat`, `/tasks/basic`)
- `src/fleet_rlm/mcp/`: optional FastMCP server

## Testing Notes

Tests mock Modal APIs and should run without cloud credentials.

- `tests/e2e/test_cli_smoke.py`
- `tests/integration/test_rlm_integration.py`
- `tests/unit/test_driver_protocol.py`
- `tests/unit/test_config.py`
- `tests/unit/test_react_agent.py`
- `tests/unit/test_react_streaming.py`
- `tests/unit/test_tools_sandbox.py`
- `tests/ui/server/*`
- `tests/ui/server/test_router_chat_tasks.py`

## Conventions

- Python 3.10+
- Type-check with `ty` (not `mypy`)
- Format/lint with `ruff`
- Prefer `uv run ...` for commands
- `serve-api` defaults to persistent Modal volume `rlm-volume-dspy` when no `interpreter.volume_name` is provided
- ReAct document tools (`load_document`, `read_file_slice`) support PDF ingestion via MarkItDown with pypdf fallback; scanned/image-only PDFs require OCR before analysis
- WebSocket interactive chat should carry identity envelope fields (`workspace_id`, `user_id`, `session_id`) so per-user/per-workspace state can be restored
- `/ws/chat` is the primary interactive path; keep ReAct as the user-facing orchestrator and delegate heavy tool execution through `RLM_DELEGATE`
- Session state manifests (logs/memory/docs/artifacts/metadata) are persisted under Modal Volume V2 paths rooted at `/data/workspaces/<workspace_id>/users/<user_id>/`

## Import Verification

- Always verify imports after any file creation or refactoring. Run `uv run python -c "import <module>"` to catch ImportErrors immediately.

## Code Quality and Debugging

- When fixing type/lint errors, first clear stale caches (`.ruff_cache/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`) and run `pre-commit clean` before making code changes.

## Task Planning

- Before creating tasks or making extensive changes, confirm the user's intent - especially for 'replan' or 'start fresh' requests.

## Modal Sandbox

- For Modal Sandbox work: always verify volume paths exist and API credentials are valid before running tests.

## Multi-Agent Workflows

- When using the teammate/RLM system: prefer using existing agents in `@.claude/agents/` rather than spawning new exploration tasks.
