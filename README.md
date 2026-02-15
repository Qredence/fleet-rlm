# fleet-rlm

[![PyPI version](https://img.shields.io/pypi/v/fleet-rlm.svg)](https://pypi.org/project/fleet-rlm/)
[![Python versions](https://img.shields.io/pypi/pyversions/fleet-rlm.svg)](https://pypi.org/project/fleet-rlm/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/Qredence/fleet-rlm/actions/workflows/ci.yml/badge.svg)](https://github.com/Qredence/fleet-rlm/actions/workflows/ci.yml)

**Secure, cloud-sandboxed Recursive Language Models (RLM) with DSPy and Modal.**

`fleet-rlm` provides a production-ready implementation of **Recursive Language Modeling** aligned with the [DSPy RLM API](https://dspy.ai/api/modules/RLM/). It gives your AI agent a secure "computer" in the cloud to read, search, and analyze massive datasets without local resource constraints.

[Paper](https://arxiv.org/abs/2501.123) | [Contributing](CONTRIBUTING.md) | [Docs](docs/)

---

## Architecture

```
User ─── CLI / API / WebSocket ─── RLMReActChatAgent (dspy.Module)
                                        │
                          ┌──────────────┼──────────────┐
                          │              │              │
                     load_document  rlm_query      edit_file
                     list_files     (recursive)    search_code
                     read_file_slice ...            ...
                          │              │
                          ▼              ▼
                   ModalInterpreter ── dspy.RLM
                          │              │
                          ▼              ▼
                   Modal Sandbox (isolated Python REPL)
                   ├── Persistent Volume (/data/)
                   ├── Execution Profiles (ROOT / DELEGATE / MAINTENANCE)
                   └── Session State (per workspace/user)
```

## Features

- **Interactive Agent**: `RLMReActChatAgent` (a `dspy.Module`) combines fast, interactive chat with deep, recursive task execution via `rlm_query`.
- **DSPy Aligned**: Implements `dspy.RLM`, `dspy.Module`, and `dspy.Tool` interfaces — compatible with DSPy optimizers (`BootstrapFewShot`, `MIPROv2`).
- **Secure Sandbox**: Code runs in isolated **Modal** containers with persistent storage volumes, execution profiles, and sensitive data redaction.
- **Recursive Delegation**: Large tasks are broken down via `rlm_query` sub-agents with depth enforcement to prevent infinite recursion.
- **PDF Ingestion**: Native document loading via MarkItDown with pypdf fallback; OCR guidance for scanned PDFs.
- **Session State**: Per-workspace, per-user session persistence with manifests stored on Modal volumes.
- **MCP Server**: Expose fleet-rlm capabilities as an MCP tool server via `serve-mcp`.
- **Observability**: Real-time streaming of thoughts, tool execution, trajectory normalization, and structured logging.

## Quick Start

### 1. Install

```bash
pip install fleet-rlm
```

Optional extras for server and MCP support:

```bash
pip install fleet-rlm[server]   # FastAPI server + WebSocket
pip install fleet-rlm[mcp]      # MCP server
pip install fleet-rlm[full]     # All extras
```

### 2. Configure

Set up your Modal and LLM credentials:

```bash
modal setup
modal volume create rlm-volume-dspy
modal secret create LITELLM DSPY_LM_MODEL=openai/gpt-4o DSPY_LLM_API_KEY=sk-...
```

### 3. Run

```bash
# Interactive chat (requires OpenTUI / Bun)
fleet-rlm code-chat --opentui

# One-shot task
fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"

# Document analysis
fleet-rlm run-architecture --docs-path docs/architecture.md --query "Extract all components"

# API server (FastAPI + WebSocket)
fleet-rlm serve-api --port 8000

# MCP server
fleet-rlm serve-mcp --transport stdio
```

## Development Setup

```bash
# Clone and install
git clone https://github.com/qredence/fleet-rlm.git
cd fleet-rlm
uv sync --extra dev

# With server/MCP support
uv sync --extra dev --extra server --extra mcp

# Copy environment template
cp .env.example .env

# Quality gate
uv run ruff check src tests && uv run ty check src && uv run pytest -q
```

## Documentation

- [Concepts](docs/explanation/rlm-concepts.md) — Core architecture (Agent, RLM, Sandbox)
- [User Flows](docs/user_flows.md) — Interaction diagrams (Chat, Tools, Delegation)
- [Architecture](docs/explanation/architecture.md) — System components and hierarchy
- [Tutorials](docs/tutorials/index.md) — Step-by-step lessons
- [How-To Guides](docs/how-to-guides/index.md) — Installation, deployment, troubleshooting
- [CLI Reference](docs/reference/cli.md) — Full CLI command reference
- [HTTP API Reference](docs/reference/http-api.md) — Server endpoints and WebSocket protocol
- [Source Layout](docs/reference/source-layout.md) — Package structure guide

## Contributing

We welcome contributions! Please see our [Contribution Guide](CONTRIBUTING.md) and run the quality gate before submitting:

```bash
uv run ruff check src tests && uv run ty check src && uv run pytest -q
```

## License

MIT License — see [LICENSE](LICENSE).

Based on [Recursive Language Modeling](https://arxiv.org/abs/2501.123) research by **Alex L. Zhang** (MIT CSAIL), **Omar Khattab** (Stanford), and **Tim Kraska** (MIT).
