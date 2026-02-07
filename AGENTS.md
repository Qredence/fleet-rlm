# AGENTS.md - RLM DSPy Modal Project

## Project Overview

This project demonstrates **DSPy's Recursive Language Model (RLM)** integrated with **Modal** for secure, cloud-based code execution. It provides both a comprehensive Jupyter notebook and an extracted Python package with a Typer CLI.

**Project Location**: repository root with package source under `rlm_content/`
**Package Name**: `fleet-rlm`
**Version**: 0.1.0

### What is RLM?

Recursive Language Models (RLM) are an inference strategy where:

- LLMs treat long contexts as an **external environment** (not input)
- The model writes Python code to programmatically explore data
- Code executes in a sandboxed environment (Modal cloud)
- Only relevant snippets are sent to sub-LLMs for semantic analysis

**Reference**: "Recursive Language Models" (Zhang, Kraska, Khattab, 2025)

---

## Technology Stack

| Component          | Technology                           |
| ------------------ | ------------------------------------ |
| Language           | Python >= 3.10                       |
| Package Manager    | `uv` (modern Python package manager) |
| Core Framework     | DSPy 3.1.3                           |
| Cloud Sandbox      | Modal                                |
| CLI Framework      | Typer >= 0.12                        |
| Testing            | pytest >= 8.2                        |
| Linting/Formatting | ruff >= 0.8                          |
| Build System       | setuptools                           |

---

## Project Structure

```
fleet-rlm-dspy/
├── pyproject.toml                    # Project configuration (root)
├── uv.lock                           # Locked dependency versions (root)
├── .env                              # Environment variables (root, local-only)
├── AGENTS.md                         # This file - project documentation
├── notebooks/
│   └── rlm-dspy-modal.ipynb          # Main Jupyter notebook (demonstration)
├── config/
│   ├── config.yaml                   # LiteLLM proxy model config
│   └── test_responses_endpoint.py    # /v1/responses validation script
└── rlm_content/
    ├── README.md
    ├── AGENTS.md
    ├── dspy-knowledge/               # DSPy documentation text
    ├── rlm-knowledge/                # RLM research paper
    ├── skills-knowledge/             # Agent skills & tools knowledge
    ├── src/rlm_dspy_modal/           # Main Python package
    └── tests/                        # Unit tests
```

---

## Module Breakdown

### `config.py` - Environment Configuration

- Loads environment variables from `.env` files
- Configures DSPy's planner LM from environment
- Guards against Modal package shadowing (prevents `modal.py` naming conflicts)
- Required env vars: `DSPY_LM_MODEL`, `DSPY_LLM_API_KEY` (or `DSPY_LM_API_KEY`)
- Optional: `DSPY_LM_API_BASE`, `DSPY_LM_MAX_TOKENS`

### `cli.py` - Typer CLI Interface

Entry points: `fleet-rlm` (preferred), `rlm-modal` (compatibility alias)

**Commands:**

- `run-basic` - Basic code generation (Fibonacci example)
- `run-architecture` - Extract DSPy architecture from docs
- `run-api-endpoints` - Extract API endpoints using batched queries
- `run-error-patterns` - Find and categorize error patterns
- `run-trajectory` - Examine execution trajectory
- `run-custom-tool` - Demo with custom regex tool
- `check-secret` - Verify Modal secret presence
- `check-secret-key` - Inspect specific secret key

### `driver.py` - Sandbox Protocol Driver

- Runs inside Modal's sandbox as a long-lived JSON protocol driver
- Handles code execution via `exec()` in a controlled environment
- Implements `SUBMIT()` function for RLM output
- Manages tool call round-trips between sandbox and host

### `interpreter.py` - ModalInterpreter

- DSPy-compatible `CodeInterpreter` implementation
- Manages Modal sandbox lifecycle (create, exec, terminate)
- Handles JSON protocol communication with the driver
- Supports custom tools and output field definitions

### `runners.py` - RLM Demo Runners

High-level functions that orchestrate complete RLM workflows:

- `run_basic()` - Simple Q&A with code generation
- `run_architecture()` - Document architecture extraction
- `run_api_endpoints()` - API endpoint extraction with batching
- `run_error_patterns()` - Multi-step error analysis
- `run_trajectory()` - Execution trajectory inspection
- `run_custom_tool()` - Custom tool demonstration
- `check_secret_presence()` / `check_secret_key()` - Secret validation

### `signatures.py` - DSPy Signatures

RLM task signatures:

- `ExtractArchitecture` - Extract modules, optimizers, design principles
- `ExtractAPIEndpoints` - Extract API endpoints with batching
- `FindErrorPatterns` - Categorize errors with solutions
- `ExtractWithCustomTool` - Use custom regex tool for extraction

### `tools.py` - Custom RLM Tools

- `regex_extract()` - Regex pattern matching tool for RLM

---

## Build and Test Commands

All commands should be run from repository root:

```bash
# Install/sync dependencies
uv sync

# Install with dev dependencies
uv sync --extra dev

# Or use Make targets
make sync
make sync-dev

# Run tests
uv run pytest
make test

# Run linting
uv run ruff check rlm_content/src rlm_content/tests config/test_responses_endpoint.py
make lint

# Format code
uv run ruff format rlm_content/src rlm_content/tests config/test_responses_endpoint.py
make format

# Install and run pre-commit hooks
uv run pre-commit install
uv run pre-commit run --all-files
make precommit-install
make precommit-run
```

---

## CLI Usage

```bash
# Show all commands
uv run fleet-rlm --help

# Run basic demo
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"

# Extract architecture from docs
uv run fleet-rlm run-architecture \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "Extract all modules and optimizers"

# Extract API endpoints
uv run fleet-rlm run-api-endpoints --docs-path rlm_content/dspy-knowledge/dspy-doc.txt

# Find error patterns
uv run fleet-rlm run-error-patterns --docs-path rlm_content/dspy-knowledge/dspy-doc.txt

# Check Modal secrets
uv run fleet-rlm check-secret
uv run fleet-rlm check-secret-key --key DSPY_LLM_API_KEY
```

---

## Jupyter Notebook Workflows

### Setup

```bash
uv sync
uv run modal setup
uv run modal volume create rlm-volume-dspy
```

### Run Notebook

```bash
uv run jupyter lab notebooks/rlm-dspy-modal.ipynb
```

### Execute Headlessly (for CI/validation)

```bash
uv run jupyter nbconvert \
  --to notebook \
  --execute \
  --inplace \
  --ExecutePreprocessor.timeout=3600 \
  notebooks/rlm-dspy-modal.ipynb
```

---

## Environment Configuration

Create `.env` file in repository root:

```bash
# Required
DSPY_LM_MODEL=openai/gemini-3-flash-preview
DSPY_LLM_API_KEY=sk-...

# Optional
DSPY_LM_API_BASE=https://your-litellm-proxy.com
DSPY_LM_MAX_TOKENS=65536
```

### Modal Secret Setup

```bash
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=... \
  DSPY_LM_API_BASE=... \
  DSPY_LLM_API_KEY=... \
  DSPY_LM_MAX_TOKENS=...
```

---

## Testing Strategy

| Test File                 | Purpose                                               |
| ------------------------- | ----------------------------------------------------- |
| `test_cli_smoke.py`       | CLI help display, command discovery, error handling   |
| `test_config.py`          | Environment loading, quoted values, fallback API keys |
| `test_driver_protocol.py` | SUBMIT output mapping, tool call round-trips          |
| `test_tools.py`           | Regex extraction, groups, flags                       |

Tests use `monkeypatch` to mock external dependencies (DSPy, Modal).

---

## Code Style Guidelines

- **Formatter**: ruff (replaces black)
- **Linter**: ruff (replaces flake8, pylint)
- **Import style**: `from __future__ import annotations` in all files
- **Type hints**: Used throughout, with `|` union syntax (Python 3.10+)
- **Docstrings**: Module-level and function-level docstrings

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ LOCAL (Jupyter/CLI)                                         │
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

---

## Key RLM Patterns

### Pattern 1: Navigate → Query → Synthesize

1. Code searches for headers in docs
2. `llm_query()` extracts info from relevant sections
3. `SUBMIT(modules=list, optimizers=list, principles=str)`

### Pattern 2: Parallel Chunk Processing

1. Split docs into chunks by headers
2. `llm_query_batched([chunk1, chunk2, ...])` - Parallel execution
3. Aggregate results

### Pattern 3: Stateful Multi-Step

1. Search for keywords
2. Save matches to variable (persists across iterations)
3. Query LLM to categorize
4. Iterate with refined queries

---

## Security Considerations

1. **Secrets Management**: All credentials stored in Modal secrets, never in code
2. **Sandbox Isolation**: Code executes in Modal's isolated sandbox environment
3. **Local .env**: Contains API keys - should be gitignored and never committed
4. **Shadow Protection**: `config.py` guards against `modal.py` naming conflicts

---

## Development Workflows

### Fresh Machine Bootstrap

1. `uv sync`
2. `uv run modal setup`
3. `uv run modal volume create rlm-volume-dspy`
4. `uv run modal secret create LITELLM ...`
5. `uv run jupyter lab notebooks/rlm-dspy-modal.ipynb`

### Daily Development

1. `uv sync`
2. `uv run pytest` - Run tests
3. `uv run fleet-rlm ...` - Test CLI commands

### Pre-Commit Validation

1. `uv run pytest` - All tests pass
2. `uv run ruff check .` - No linting errors
3. `uv run ruff format .` - Code formatted

---

## Troubleshooting

### Issue: "Planner LM not configured"

**Fix**: Set `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` in `.env`, restart shell/kernel

### Issue: "Modal sandbox process exited unexpectedly"

**Fix**:

```bash
uv run modal token set
uv run modal volume list
```

### Issue: "No module named 'modal'"

**Fix**: `uv sync`

### Issue: Modal package shadowing

**Fix**: Remove any `modal.py` file or `__pycache__/modal.*.pyc` in working directory

---

## References

- **RLM Paper**: [Recursive Language Models](https://arxiv.org/abs/2501.123)
- **DSPy Docs**: https://dspy-docs.vercel.app/
- **Modal Docs**: https://modal.com/docs
- **UV Docs**: https://docs.astral.sh/uv/

---

## Changelog

| Date       | Change                                          |
| ---------- | ----------------------------------------------- |
| 2026-02-06 | Initial notebook with Modal sandbox integration |
| 2026-02-06 | Extracted Python package with Typer CLI         |
| 2026-02-06 | Added pytest test suite                         |
| 2026-02-06 | Added ruff for linting/formatting               |

---

_Last updated: 2026-02-07_
