# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

fleet-rlm is a Python package implementing **Recursive Language Models (RLM)** with DSPy and Modal for secure, cloud-based code execution. It enables LLMs to treat long contexts as external environments, using programmatic code exploration in sandboxed Modal environments.

Reference: [Recursive Language Models paper](https://arxiv.org/abs/2501.123) (Zhang, Kraska, Khattab, 2025)

## Setup & Configuration

### Initial Setup

```bash
uv sync --extra dev                              # Base dev environment
uv sync --extra dev --extra interactive          # code-chat runtime
uv sync --extra dev --extra interactive --extra mcp --extra server  # full optional surface
cp .env.example .env         # Configure DSPY_LM_MODEL, DSPY_LLM_API_KEY
```

### Modal Setup (Required for RLM execution)

```bash
# Authenticate with Modal (per-user)
uv run modal setup

# Create a Modal volume for data persistence (v2 recommended)
uv run modal volume create rlm-volume-dspy

# Create Modal secret for API keys
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=... \
  DSPY_LM_API_BASE=... \
  DSPY_LLM_API_KEY=...
```

### Environment Configuration

Required environment variables (in `.env`):

- `DSPY_LM_MODEL` - Model identifier (e.g., `openai/gpt-4`, `google/gemini-3-flash-preview`)
- `DSPY_LLM_API_KEY` or `DSPY_LM_API_KEY` - API key for the LLM provider

Optional:

- `DSPY_LM_API_BASE` - Custom API endpoint
- `DSPY_LM_MAX_TOKENS` - Max tokens (default: 16000)

## Common Commands

### CLI Usage

```bash
# Show all available commands
uv run fleet-rlm --help

# Run basic demo
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"

# Extract architecture from documentation
uv run fleet-rlm run-architecture \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "Extract all modules and optimizers"

# Interactive ReAct chat (Textual TUI by default)
uv run fleet-rlm code-chat --docs-path rlm_content/dspy-knowledge/dspy-doc.txt

# OpenTUI React frontend (requires Bun runtime)
uv run fleet-rlm code-chat --opentui

# Legacy prompt-toolkit fallback
uv run fleet-rlm code-chat --legacy

# Check Modal secrets are configured
uv run fleet-rlm check-secret
```

### API Server (requires `--extra server`)

```bash
# Dev server with hot reload
uv run fastapi dev src/fleet_rlm/server/main.py

# Production server via CLI
uv run fleet-rlm serve-api --port 8000

# MCP server (requires `--extra mcp`)
uv run fleet-rlm serve-mcp --transport stdio
```

### Development Workflow

```bash
# Run all tests (uses mocked Modal - no cloud calls)
uv run pytest

# Run a specific test file
uv run pytest tests/test_config.py -v

# Run linting
uv run ruff check src tests

# Format code
uv run ruff format src tests

# Type check (use ty, never mypy)
uv run ty check src

# Run all checks (lint + test)
make check

# Run release validation
make release-check
```

### Skills and Agents Management

```bash
# Install bundled skills/agents/teams/hooks to ~/.claude/
uv run fleet-rlm init

# List available scaffold assets
uv run fleet-rlm init --list

# Sync scaffold files to package (after modifying .claude/)
make sync-scaffold
```

For Claude Code agent teams, set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in Claude settings or shell env.

## Architecture

### Core Components

The package follows a layered architecture where DSPy RLM orchestrates code generation that executes in Modal sandboxes:

```
┌─────────────────────────────────────────────────────────────┐
│ LOCAL (CLI/Jupyter/Server)                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Planner LM  │  │ RLM Module   │  │ ModalInterpreter │   │
│  │ (decides    │→ │ (builds      │→ │ (manages sandbox │   │
│  │  what code  │  │  signatures) │  │  lifecycle)      │   │
│  │  to write)  │  │              │  │                  │   │
│  └─────────────┘  └──────────────┘  └──────────────────┘   │
│           │                │                  │             │
│           │                │ JSON stdin       │ gRPC        │
│           │                ↓                  ↓             │
└───────────┼────────────────┼──────────────────┼─────────────┘
            │                │                  │
            │                │                  ▼
            │                │     ┌──────────────────────┐
            │                │     │ MODAL CLOUD          │
            │                │     │  ┌────────────────┐  │
            │                └────→│  │ Sandbox        │  │
            │                      │  │ - Python 3.12  │  │
            │                      │  │ - Volume /data │  │
            │                      │  │ - Secrets      │  │
            │                      │  └────────────────┘  │
            │                      │           │          │
            │                      │           ▼          │
            │                      │  ┌────────────────┐  │
            │                      │  │ Driver Process │  │
            │                      │  │ - exec() code  │  │
            │                      │  │ - tool bridging│  │
            │                      │  └────────────────┘  │
            │                      └──────────────────────┘
            │                                │
            └────────────────────────────────┘
                        tool_call requests
                        (llm_query, etc.)
```

### Key Modules

- **`config.py`**: Environment configuration with `.env` loading and Modal shadowing protection. Guards against local `modal.py` files that would shadow the Modal package. Provides `configure_planner_from_env()` and async-safe `get_planner_lm_from_env()`.

- **`interpreter.py`**: `ModalInterpreter` class - the main DSPy CodeInterpreter implementation that manages Modal sandbox lifecycle, tool registration, and JSON protocol communication. Supports volume persistence, stdout summarization (per RLM paper Section 2), and sub-LLM call limiting.

- **`driver.py`**: `sandbox_driver()` function that runs inside the Modal sandbox, executing Python code via `exec()` and handling the JSON protocol for tool calls. Injected helpers: `peek()`, `grep()`, `chunk_by_*()`, buffers, volume persistence, workspace functions.

- **`signatures.py`**: DSPy signatures defining input/output fields for RLM tasks (ExtractArchitecture, ExtractAPIEndpoints, FindErrorPatterns, AnalyzeLongDocument, SummarizeLongDocument, etc.).

- **`runners.py`**: High-level orchestration functions for complete RLM workflows. Includes `build_react_chat_agent()` for interactive ReAct chat sessions.

- **`react_agent.py`**: `RLMReActChatAgent` - conversational ReAct agent with RLM tools, document loading, and chat history management.

- **`chunking.py`**: Pure functions for text chunking (by size, headers, timestamps, JSON keys). These are stdlib-only and mirrored in the sandbox driver.

- **`cli.py`**: Typer-based CLI with demo commands, interactive chat, API server, and MCP server commands.

- **`scaffold.py`**: Functions for installing bundled skills, agents, teams, and hooks to `~/.claude/`.

- **`server/`**: FastAPI server package (requires `--extra server`):
  - `main.py`: App factory with lifespan management and Scalar docs
  - `config.py`: `ServerRuntimeConfig` Pydantic model
  - `deps.py`: `ServerState` singleton and FastAPI Depends helpers
  - `schemas.py`: Pydantic request/response models
  - `middleware.py`: CORS and request-id middleware
  - `routers/`: Health, chat, WebSocket, and task endpoints

- **`mcp/`**: FastMCP server implementation (requires `--extra mcp`)

### Long-Context RLM Workflow

The `.claude/skills/rlm-long-context/` scripts handle large contexts:

1. **Chunking**: Content split using semantic boundaries
2. **Ranking**: Chunks ranked by query relevance
3. **Caching**: Results cached per chunk+query
4. **Processing**: Subagents process in relevance order
5. **Early Exit**: Stops when confidence threshold reached

### JSON Protocol

The interpreter communicates with the sandbox via a line-delimited JSON protocol:

**Input (to sandbox):**

```json
{ "code": "python code", "variables": {}, "tool_names": [], "output_names": [] }
```

**Output (from sandbox):**

```json
{ "stdout": "...", "stderr": "...", "final": { "structured": "output" } }
```

**Tool calls (output from sandbox):**

```json
{ "tool_call": { "name": "llm_query", "args": ["prompt"], "kwargs": {} } }
```

**Tool responses (input to sandbox):**

```json
{"tool_result": "..."} or {"tool_error": "..."}
```

### Sandbox-Injectable Functions

Functions injected into the sandbox globals for LLM-generated code to use:

- `peek(text, start, length)` - Return a slice of text
- `grep(text, pattern, context=0)` - Search lines case-insensitively
- `chunk_by_size(text, size, overlap=0)` - Split into fixed-size chunks
- `chunk_by_headers(text, pattern)` - Split at markdown headers
- `chunk_by_timestamps(text, pattern)` - Split logs at timestamps
- `chunk_by_json_keys(text)` - Split JSON objects by top-level key
- `add_buffer(name, value)` / `get_buffer(name)` / `clear_buffer(name)` - Stateful accumulation
- `save_to_volume(path, content)` / `load_from_volume(path)` - Persist to `/data/`
- `workspace_write(path, content)` / `workspace_read(path)` / `workspace_list()` - Workspace under `/data/workspace/`
- `SUBMIT(*args, **kwargs)` - Return structured final output
- `llm_query(prompt)` / `llm_query_batched(prompts)` - Sub-LLM calls (via tool_call bridge)

### Skills and Agents

The package bundles Claude Code skills and agents in `.claude/`:

**Skills** (in `.claude/skills/`):

- `rlm` - Main skill for long-context RLM tasks
- `rlm-run`, `rlm-batch`, `rlm-debug`, `rlm-execute`, `rlm-memory` - Specialized RLM operations
- `rlm-long-context` - EXPERIMENTAL research implementation
- `rlm-test-suite` - Testing and evaluation
- `modal-sandbox` - Sandbox management
- `dspy-signature` - Signature generation

**Agents** (in `.claude/agents/`):

- `rlm-orchestrator` - Multi-agent coordination (recommended for complex tasks)
- `rlm-specialist` - Complex RLM execution
- `rlm-subcall` - Lightweight sub-LLM calls
- `modal-interpreter-agent` - Direct sandbox interaction

**Hooks** (in `.claude/hooks/`):

- Prompt hooks for document processing, large-file workflows, and error troubleshooting

These are synced to `src/fleet_rlm/_scaffold/` for packaging and installed to `~/.claude/` via `fleet-rlm init`.

## Key Design Patterns & Code Style

**Metadata-Only History** (RLM paper Section 2):
Long stdout outputs are summarized to prevent context window pollution:

```
[Output: 1,247 chars, 42 lines]
Prefix: "First 200 chars of output..."
```

**Final Variable Convention**:
Code can signal completion by setting a variable named `Final`:

```python
analysis = process_document(text)
Final = {"result": analysis, "status": "complete"}
```

**Stateful Execution**:
Globals persist across `execute()` calls for incremental workflows. Use `add_buffer()`/`get_buffer()` for accumulating results across iterations.

**Async-Safe Configuration**:
For async contexts (FastAPI, etc.), use `get_planner_lm_from_env()` which returns an LM without calling `dspy.configure()`. Use `dspy.context(lm=lm)` for thread-local configuration.

**Validation & Style**:

- Python 3.10+, strict typing with `ty` (never use `mypy` directly).
- Sentinel value: `python_version < '3.11'`.
- Format with `ruff format`, lint with `ruff check`.
- No hardcoded secrets—use Modal secrets or `.env`.
- Sandbox-injectable functions must be pure stdlib-only (see `driver.py`).

## Interactive ReAct chat pattern

- `RLMReActChatAgent` defines `agent = dspy.ReAct(...)` with specialized tools
- Chat memory is `dspy.History`; sandbox memory uses buffers + optional Volume V2
- `code-chat` / `run-react-chat` launch Textual by default, with `--legacy` for prompt-toolkit
- Trace defaults to `compact`; use `--trace-mode compact|verbose|off` (`--trace` and `--no-trace` still work)
- Textual keybindings: `Ctrl+C` cancel turn, `Ctrl+L` clear panes, `F2` reasoning pane, `F3` tools pane

## Troubleshooting

| Issue                | Fix                                                     |
| -------------------- | ------------------------------------------------------- |
| Missing planner LM   | Check `.env` has `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` |
| Modal auth error     | Run `uv run modal token set`                            |
| Import shadows modal | Delete any local `modal.py` file                        |
| Volume not found     | Run `uv run modal volume create rlm-volume-dspy`        |

## Testing

Tests use a mocked Modal environment to avoid requiring actual Modal credentials:

- `test_rlm_integration.py` - Integration tests with mocked sandbox
- `test_driver_protocol.py` - JSON protocol testing
- `test_config.py` - Environment loading tests
- `test_scaffold.py` - Skills/agents installation tests
- `test_react_agent.py` - ReAct chat agent tests
- `test_textual_app.py` - Textual UI tests (requires `textual` extra)
- `test_server_*.py` - FastAPI server tests

The test suite patches `modal.Sandbox` and related classes to simulate sandbox behavior without cloud calls.
