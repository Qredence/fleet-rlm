# fleet-rlm

A Python package implementing **Recursive Language Models (RLM)** with DSPy and Modal for secure, cloud-based code execution. This project demonstrates how LLMs can treat long contexts as external environments, using programmatic code exploration in sandboxed environments.

**Reference**: [Recursive Language Models](https://arxiv.org/abs/2501.123) (Zhang, Kraska, Khattab, 2025)

---

## Overview

Recursive Language Models (RLM) represent an inference strategy where:

- LLMs treat long contexts as an **external environment** rather than direct input
- The model writes Python code to programmatically explore data
- Code executes in a sandboxed environment (Modal cloud)
- Only relevant snippets are sent to sub-LLMs for semantic analysis

This package provides both a comprehensive Jupyter notebook and a Typer CLI for running RLM workflows.

---

## Using dspy.RLM with Claude Code

`fleet-rlm` is designed to work seamlessly with **Claude Code** (Claude's agentic coding capabilities). The bundled skills, agents, team templates, and hooks enable Claude to leverage `dspy.RLM` for complex, long-context tasks.

### Why Use RLM with Claude Code?

| Challenge | RLM Solution |
|-----------|-------------|
| Context window limits | Code explores data programmatically instead of loading everything |
| Complex multi-step analysis | Sandbox code can delegate semantic work via `llm_query()` / `llm_query_batched()` (and Claude subagents can orchestrate this workflow) |
| Reproducibility | All exploration steps are Python code — auditable and replayable |
| Secure execution | Modal sandboxes isolate untrusted code from your environment |

### Agent Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ CLAUDE CODE (Orchestrator)                                  │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │ rlm-orchestrator│  │ rlm-specialist  │                  │
│  │ (coordinates    │→ │ (executes       │                  │
│  │  multi-agent    │  │  complex RLM    │                  │
│  │  workflows)     │  │  tasks)         │                  │
│  └─────────────────┘  └─────────────────┘                  │
│           │                    │                            │
│           ▼                    ▼                            │
│  ┌─────────────────────────────────────────┐               │
│  │ ModalInterpreter (dspy.CodeInterpreter) │               │
│  │  • Manages sandbox lifecycle            │               │
│  │  • Bridges tools to/from sandbox        │               │
│  │  • Handles llm_query() sub-calls        │               │
│  └─────────────────────────────────────────┘               │
│                        │                                    │
└────────────────────────┼────────────────────────────────────┘
                         │ JSON protocol
                         ▼
              ┌──────────────────────┐
              │ MODAL SANDBOX        │
              │  • Python 3.12       │
              │  • Isolated exec()   │
              │  • Sub-LLM calls     │
              └──────────────────────┘
```

### Quick Start with Claude Code

1. **Install scaffold assets** to your Claude configuration:
   ```bash
   uv run fleet-rlm init
   ```

   This installs skills, agents, teams, and hooks to `~/.claude/` by default.

2. **Use the `rlm` skill** in Claude Code for long-context tasks:
   ```
   @rlm Analyze this 500KB log file and extract all error patterns
   ```

3. **Use the `rlm-orchestrator` agent** for multi-step workflows:
   ```
   @rlm-orchestrator Process these 10 documentation files, 
   extract API endpoints, and generate a summary report
   ```

### Sub-LLM Patterns

The RLM approach enables semantic delegation from sandbox code:

```python
# Inside Modal sandbox, the LLM-generated code can call:
result = llm_query("Extract all function signatures from this code snippet")

# Or batch multiple sub-queries in parallel:
results = llm_query_batched([
    "Summarize section 1",
    "Summarize section 2",
])
```

In Claude workflows, the packaged `rlm-subcall` agent can be used as an orchestration pattern around these calls.

This pattern allows you to:
- **Delegate semantic analysis** to sub-LLMs while keeping orchestration logic in Python
- **Process chunks in parallel** for large documents
- **Accumulate results** across multiple iterations using stateful buffers

---

## Features

- **Secure Cloud Execution**: Code runs in Modal's isolated sandbox environment
- **DSPy Integration**: Built on DSPy 3.1.3 with custom signatures for RLM tasks
- **CLI Interface**: Typer-based CLI with multiple demo commands
- **Extensible Tools**: Support for custom tools that bridge sandbox and host
- **Secret Management**: Secure handling of API keys via Modal secrets

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

---

## Installation

```bash
# Clone the repository
git clone https://github.com/qredence/fleet-rlm.git
cd fleet-rlm

# Install dependencies with uv
uv sync

# For development (includes test tools)
uv sync --extra dev
```

### Scaffold Installation

`fleet-rlm` includes custom Claude skills, agents, team templates, and hooks optimized for RLM workflows. Install them to your user directory for use across all projects:

```bash
# List available scaffold assets
uv run fleet-rlm init --list

# Install all scaffold assets to ~/.claude/
uv run fleet-rlm init

# Or install to a custom directory
uv run fleet-rlm init --target ~/.config/claude

# Force overwrite existing files
uv run fleet-rlm init --force

# Install only team templates
uv run fleet-rlm init --teams-only

# Install only hooks
uv run fleet-rlm init --hooks-only

# Install all except hooks
uv run fleet-rlm init --no-hooks
```

**Note**: Scaffold assets are workflow definitions only. You still need to configure Modal authentication and secrets separately (see [Setup Modal](#2-setup-modal) above).
**Agent Teams** are experimental in Claude Code and require setting `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in Claude settings or your environment. Team config lives under `~/.claude/teams/{team}/config.json`; runtime task state is managed by Claude under `~/.claude/tasks/{team}/`.

**Available Skills:**

- `dspy-signature` - Generate and validate DSPy signatures
- `modal-sandbox` - Manage Modal sandboxes
- `rlm` - Run RLM for long-context tasks
- `rlm-batch` - Execute parallel tasks
- `rlm-debug` - Debug RLM execution
- `rlm-execute` - Execute Python in sandboxes
- `rlm-long-context` - (EXPERIMENTAL) Research implementation
- `rlm-memory` - Long-term memory persistence
- `rlm-run` - Run RLM tasks with proper configuration
- `rlm-test-suite` - Test and evaluate workflows

**Available Agents:**

- `modal-interpreter-agent` - Direct Modal sandbox interaction
- `rlm-orchestrator` - Multi-agent RLM coordination (recommended for complex tasks)
- `rlm-specialist` - Complex RLM task execution with full sandbox access
- `rlm-subcall` - Lightweight sub-LLM calls for delegation patterns

**Available Team Templates:**

- `fleet-rlm` - Preconfigured multi-agent team layout for RLM orchestration

**Available Hooks:**

- `hookify.fleet-rlm-document-process.local.md` - Prompt hook for document processing workflows
- `hookify.fleet-rlm-large-file.local.md` - Prompt hook for large-file workflow suggestions
- `hookify.fleet-rlm-llm-query-error.local.md` - Prompt hook for llm_query troubleshooting guidance
- `hookify.fleet-rlm-modal-error.local.md` - Prompt hook for Modal/sandbox troubleshooting guidance

> **Tip**: Start with `rlm-orchestrator` for multi-file or multi-step tasks. It coordinates `rlm-specialist` and `rlm-subcall` automatically.

---

## Quick Start

### 1. Configure Environment

Copy the environment template and fill in your values:

```bash
# Copy the template
cp .env.example .env

# Edit with your API keys and model configuration
# See .env.example for detailed documentation on each variable
vim .env
```

**Required variables:**

- `DSPY_LM_MODEL` - LLM model identifier (e.g., `openai/gpt-4`, `google/gemini-3-flash-preview`)
- `DSPY_LLM_API_KEY` - API key for your LLM provider

**Optional variables:**

- `DSPY_LM_API_BASE` - Custom API endpoint (if using proxy or self-hosted)
- `DSPY_LM_MAX_TOKENS` - Maximum response length (default: 8192)

⚠️ **Security**: The `.env` file is gitignored and will never be committed. Keep your API keys safe!

### 2. Setup Modal

**Important**: Modal authentication and secrets are **per user**. Each developer needs to run Modal setup in their own environment and create secrets in their own Modal account. Credentials are not shared or bundled with `fleet-rlm`.

```bash
# Authenticate with Modal
uv run modal setup

# Create a Modal volume for data
uv run modal volume create rlm-volume-dspy

# Create Modal secret for API keys
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=... \
  DSPY_LM_API_BASE=... \
  DSPY_LLM_API_KEY=... \
  DSPY_LM_MAX_TOKENS=...
```

### 3. Run CLI Commands

```bash
# Show all available commands
uv run fleet-rlm --help

# Run a basic demo
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"

# Doc-analysis commands require --docs-path
# Extract architecture from documentation
uv run fleet-rlm run-architecture \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "Extract all modules and optimizers"

# Extract API endpoints
uv run fleet-rlm run-api-endpoints --docs-path rlm_content/dspy-knowledge/dspy-doc.txt

# Find error patterns
uv run fleet-rlm run-error-patterns --docs-path rlm_content/dspy-knowledge/dspy-doc.txt

# Inspect trajectory on a document sample
uv run fleet-rlm run-trajectory \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --chars 5000

# Use custom regex tool
uv run fleet-rlm run-custom-tool \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --chars 5000

# Analyze or summarize long-context docs with sandbox helpers
uv run fleet-rlm run-long-context \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "What are the main design decisions?" \
    --mode analyze

# Check Modal secrets are configured
uv run fleet-rlm check-secret
```

---

## CLI Commands

| Command              | Description                                  |
| -------------------- | -------------------------------------------- |
| `init`               | Install bundled Claude scaffold assets       |
| `run-basic`          | Basic code generation (Fibonacci example)    |
| `run-architecture`   | Extract DSPy architecture from documentation |
| `run-api-endpoints`  | Extract API endpoints from documentation      |
| `run-error-patterns` | Find and categorize error patterns in docs   |
| `run-trajectory`     | Examine RLM execution trajectory             |
| `run-custom-tool`    | Demo with custom regex tool                  |
| `run-long-context`   | Analyze or summarize a long document         |
| `check-secret`       | Verify Modal secret presence                 |
| `check-secret-key`   | Inspect specific secret key                  |

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

## Package Structure

```
src/fleet_rlm/
├── __init__.py      # Package exports
├── cli.py           # Typer CLI interface
├── config.py        # Environment configuration
├── driver.py        # Sandbox protocol driver
├── interpreter.py   # ModalInterpreter implementation
├── runners.py       # High-level RLM demo runners
├── signatures.py    # DSPy signatures for RLM tasks
└── tools.py         # Custom RLM tools
```

### Module Descriptions

- **`config.py`**: Loads environment variables, configures DSPy's planner LM, guards against Modal package shadowing
- **`cli.py`**: Typer CLI with commands for running demos and checking secrets
- **`driver.py`**: Runs inside Modal's sandbox as a long-lived JSON protocol driver
- **`interpreter.py`**: DSPy-compatible `CodeInterpreter` managing Modal sandbox lifecycle
- **`runners.py`**: High-level functions orchestrating complete RLM workflows
- **`signatures.py`**: RLM task signatures (ExtractArchitecture, ExtractAPIEndpoints, etc.)
- **`tools.py`**: Custom tools like `regex_extract()` for RLM use

---

## RLM Patterns

### Pattern 1: Navigate → Query → Synthesize

1. Code searches for headers in documentation
2. `llm_query()` extracts info from relevant sections
3. `SUBMIT(modules=list, optimizers=list, principles=str)` returns structured output

### Pattern 2: Parallel Chunk Processing

1. Split documents into chunks by headers
2. `llm_query_batched([chunk1, chunk2, ...])` executes in parallel
3. Aggregate results into final output

### Pattern 3: Stateful Multi-Step

1. Search for keywords in documentation
2. Save matches to variable (persists across iterations)
3. Query LLM to categorize findings
4. Iterate with refined queries

---

## Testing

```bash
# Run all tests
uv run pytest

# Or via Make
make test
```

| Test File                 | Purpose                                               |
| ------------------------- | ----------------------------------------------------- |
| `test_cli_smoke.py`       | CLI help display, command discovery, error handling   |
| `test_config.py`          | Environment loading, quoted values, fallback API keys |
| `test_driver_protocol.py` | SUBMIT output mapping, tool call round-trips          |
| `test_tools.py`           | Regex extraction, groups, flags                       |

---

## Development

```bash
# Install dev dependencies
make sync-dev

# Run linting
make lint

# Format code
make format

# Run all checks
make check

# Run release validation (lint, tests, build, twine check)
make release-check

# Install pre-commit hooks
make precommit-install
make precommit-run
```

Release process documentation is in [`RELEASING.md`](scripts/RELEASING.md), including the TestPyPI-first workflow.

---

## Jupyter Notebook

The original implementation is available as a Jupyter notebook:

```bash
# Launch Jupyter Lab
uv run jupyter lab notebooks/rlm-dspy-modal.ipynb

# Execute headlessly (for CI/validation)
uv run jupyter nbconvert \
  --to notebook \
  --execute \
  --inplace \
  --ExecutePreprocessor.timeout=3600 \
  notebooks/rlm-dspy-modal.ipynb
```

---

## Security

- **Secrets Management**: All credentials stored in Modal secrets, never in code
- **Sandbox Isolation**: Code executes in Modal's isolated sandbox environment
- **Local .env**: Contains API keys - is gitignored and should never be committed
- **Shadow Protection**: `config.py` guards against `modal.py` naming conflicts

---

## Troubleshooting

### "Planner LM not configured"

Set `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` in `.env`, then restart your shell or kernel.

### "Modal sandbox process exited unexpectedly"

```bash
uv run modal token set
uv run modal volume list
```

### "No module named 'modal'"

```bash
uv sync
```

### Modal package shadowing

Remove any `modal.py` file or `__pycache__/modal.*.pyc` in the working directory.

---

## References

- **RLM Paper**: [Recursive Language Models](https://arxiv.org/abs/2501.123) (also available in `rlm-volume-dspy` at `/data/rlm-knowledge/`)
- **DSPy Docs**: https://dspy-docs.vercel.app/
- **Modal Docs**: https://modal.com/docs
- **UV Docs**: https://docs.astral.sh/uv/
- **Project Docs**: See `docs/` directory for detailed guides and architecture documentation

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## Acknowledgments

This project is based on research from the [Recursive Language Models paper](https://arxiv.org/abs/2501.123) by Zhang, Kraska, and Khattab (2025).

Built with:

- [DSPy](https://dspy-docs.vercel.app/) - Framework for programming with language models
- [Modal](https://modal.com/) - Serverless computing for AI
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) - Agentic coding with Claude
