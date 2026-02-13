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
```

## Common Commands

```bash
# from repo root
uv run fleet-rlm --help
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"
uv run fleet-rlm run-architecture --docs-path rlm_content/dspy-knowledge/dspy-doc.txt --query "Extract all modules and optimizers"
uv run fleet-rlm code-chat --opentui
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm serve-mcp --transport stdio
uv run pytest
uv run ruff check src tests
uv run ruff format src tests
uv run ty check src
```

## Interactive Surface

- OpenTUI under `tui/` is the active and only supported interactive runtime.
- Python Textual and legacy prompt-toolkit runtimes have been removed.

## Architecture Highlights

- `src/fleet_rlm/core/config.py`: env loading + planner LM configuration
- `src/fleet_rlm/core/interpreter.py`: `ModalInterpreter` lifecycle + JSON bridge + execution profiles (`ROOT_INTERLOCUTOR`, `RLM_DELEGATE`, `MAINTENANCE`)
- `src/fleet_rlm/core/driver.py`: sandbox-side execution driver, profile-aware helper/tool gating, and Final/SUBMIT extraction
- `src/fleet_rlm/react/agent.py`: `RLMReActChatAgent`
- `src/fleet_rlm/react/tools.py`: ReAct tool definitions
- `src/fleet_rlm/runners.py`: high-level task runners
- `src/fleet_rlm/cli.py`: Typer CLI entrypoint
- `src/fleet_rlm/server/`: optional FastAPI server
- `src/fleet_rlm/mcp/`: optional FastMCP server

## Testing Notes

Tests mock Modal APIs and should run without cloud credentials.

- `tests/e2e/test_cli_smoke.py`
- `tests/integration/test_rlm_integration.py`
- `tests/unit/test_driver_protocol.py`
- `tests/ui/server/*`

## Conventions

- Python 3.10+
- Type-check with `ty` (not `mypy`)
- Format/lint with `ruff`
- Prefer `uv run ...` for commands
- ReAct document tools (`load_document`, `read_file_slice`) support PDF ingestion via MarkItDown with pypdf fallback; scanned/image-only PDFs require OCR before analysis
- WebSocket interactive chat should carry identity envelope fields (`workspace_id`, `user_id`, `session_id`) so per-user/per-workspace state can be restored
- `/ws/chat` is the primary interactive path; keep ReAct as the user-facing orchestrator and delegate heavy tool execution through `RLM_DELEGATE`
- Session state manifests (logs/memory/docs/artifacts/metadata) are persisted under Modal Volume V2 paths rooted at `/data/workspaces/<workspace_id>/users/<user_id>/`
