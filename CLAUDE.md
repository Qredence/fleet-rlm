# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

fleet-rlm is a Python package implementing Recursive Language Models (RLM) with DSPy and Modal for secure, cloud-based code execution. It enables LLMs to treat long contexts as external environments, using programmatic code exploration in sandboxed Modal environments.

## Common Commands

### Development Workflow
```bash
# Install dependencies
uv sync

# Install with dev dependencies (includes pytest, ruff, pre-commit)
uv sync --extra dev

# Run all tests
uv run pytest

# Run a specific test file
uv run pytest tests/test_config.py

# Run linting
uv run ruff check src tests

# Format code
uv run ruff format src tests

# Run all checks (lint + test)
make check

# Run release validation
make release-check
```

### CLI Usage
```bash
# Show all available commands
uv run fleet-rlm --help

# Run basic demo
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"

# Run architecture extraction from docs
uv run fleet-rlm run-architecture \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "Extract all modules and optimizers"

# Check Modal secrets are configured
uv run fleet-rlm check-secret
```

### Skills and Agents Management
```bash
# Install bundled skills/agents to ~/.claude/
uv run fleet-rlm init

# List available skills/agents
uv run fleet-rlm init --list

# Sync scaffold files to package (after modifying .claude/)
make sync-scaffold
```

### Modal Setup (Required for RLM execution)
```bash
# Authenticate with Modal
uv run modal setup

# Create a Modal volume for data
uv run modal volume create rlm-volume-dspy

# Create Modal secret for API keys
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=... \
  DSPY_LM_API_BASE=... \
  DSPY_LLM_API_KEY=...
```

## Architecture

### Core Components

The package follows a layered architecture where DSPy RLM orchestrates code generation that executes in Modal sandboxes:

```
┌─────────────────────────────────────────────────────────────┐
│ LOCAL (CLI/Jupyter)                                         │
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

- **`config.py`**: Environment configuration with `.env` loading and Modal shadowing protection. Guards against local `modal.py` files that would shadow the Modal package.
- **`interpreter.py`**: `ModalInterpreter` class - the main DSPy CodeInterpreter implementation that manages Modal sandbox lifecycle, tool registration, and JSON protocol communication.
- **`driver.py`**: `sandbox_driver()` function that runs inside the Modal sandbox, executing Python code via `exec()` and handling the JSON protocol for tool calls.
- **`signatures.py`**: DSPy signatures defining input/output fields for RLM tasks (ExtractArchitecture, ExtractAPIEndpoints, etc.).
- **`runners.py`**: High-level orchestration functions for complete RLM workflows.
- **`cli.py`**: Typer-based CLI with demo commands.
- **`scaffold.py`**: Functions for installing bundled skills and agents to `~/.claude/`.

### JSON Protocol

The interpreter communicates with the sandbox via a line-delimited JSON protocol:

**Input (to sandbox):**
```json
{"code": "python code", "variables": {}, "tool_names": [], "output_names": []}
```

**Output (from sandbox):**
```json
{"stdout": "...", "stderr": "...", "final": {"structured": "output"}}
```

**Tool calls:**
```json
{"tool_call": {"name": "tool_name", "args": [], "kwargs": {}}}
```

### Skills and Agents

The package bundles Claude Code skills and agents in `.claude/`:

**Skills** (in `.claude/skills/`):
- `rlm` - Main skill for long-context RLM tasks
- `rlm-run`, `rlm-batch`, `rlm-debug`, `rlm-execute`, `rlm-memory` - Specialized RLM operations
- `modal-sandbox` - Sandbox management
- `dspy-signature` - Signature generation

**Agents** (in `.claude/agents/`):
- `rlm-orchestrator` - Multi-agent coordination (recommended for complex tasks)
- `rlm-specialist` - Complex RLM execution
- `rlm-subcall` - Lightweight sub-LLM calls
- `modal-interpreter-agent` - Direct sandbox interaction

These are synced to `src/fleet_rlm/_scaffold/` for packaging and installed to `~/.claude/` via `fleet-rlm init`.

## Environment Configuration

Required environment variables (in `.env`):
- `DSPY_LM_MODEL` - Model identifier (e.g., `openai/gpt-4`, `google/gemini-3-flash-preview`)
- `DSPY_LLM_API_KEY` - API key for the LLM provider

Optional:
- `DSPY_LM_API_BASE` - Custom API endpoint
- `DSPY_LM_MAX_TOKENS` - Max tokens (default: 16000)

Modal credentials are configured separately via `modal setup` and Modal secrets.

## Testing

Tests use a mocked Modal environment to avoid requiring actual Modal credentials:
- `test_rlm_integration.py` - Integration tests with mocked sandbox
- `test_driver_protocol.py` - JSON protocol testing
- `test_config.py` - Environment loading tests
- `test_scaffold.py` - Skills/agents installation tests

The test suite patches `modal.Sandbox` and related classes to simulate sandbox behavior without cloud calls.
