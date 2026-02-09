# AGENTS.md

RLM (Recursive Language Model) implementation using DSPy + Modal for sandboxed code execution.

## Setup

```bash
uv sync --extra dev          # Install all dependencies
cp .env.example .env         # Configure DSPY_LM_MODEL, DSPY_LLM_API_KEY
uv run modal setup           # Authenticate with Modal (per-user)
uv run modal secret create LITELLM DSPY_LM_MODEL=... DSPY_LLM_API_KEY=...
```

## Commands

```bash
uv run fleet-rlm --help                    # Show all CLI commands
uv run fleet-rlm run-basic                 # Test sandbox with Fibonacci
uv run fleet-rlm check-secret              # Verify Modal secrets
uv run pytest                              # Run tests (mocked Modal)
uv run ruff check src tests                # Lint
uv run ruff format src tests               # Format
uv run ty check src                        # Type check (use ty, never mypy)
```

## Architecture Overview

### Component Diagram

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   CLI (Typer)   │────▶│  Modal Sandbox   │────▶│  DSPy Planner   │
│   cli.py        │     │  interpreter.py  │     │  config.py      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                             │
                             ▼
                      ┌──────────────────┐
                      │  Sandbox Driver  │
                      │  driver.py       │
                      │  (runs in Modal) │
                      └──────────────────┘
```

### Long-Context RLM Workflow

The `.claude/skills/rlm-long-context/` scripts handle large contexts:

1. **Chunking**: Content split using semantic boundaries
2. **Ranking**: Chunks ranked by query relevance
3. **Caching**: Results cached per chunk+query
4. **Processing**: Subagents process in relevance order
5. **Early Exit**: Stops when confidence threshold reached

### Key Design Patterns

**Sandbox-Injectable Functions**: Functions in `chunking.py` are pure (stdlib-only) for sandbox injection.

**JSON Protocol**: Sandbox communicates via JSON lines on stdin/stdout.

**Stateful Execution**: Globals persist across calls for incremental workflows.

## Project Layout

```
src/fleet_rlm/
├── cli.py           # Typer CLI entry point
├── config.py        # Environment & DSPy planner setup
├── driver.py        # Sandbox driver (runs inside Modal)
├── interpreter.py   # ModalInterpreter (CodeInterpreter protocol)
├── runners.py       # Workflow orchestrators for demos
├── signatures.py    # DSPy Signatures (Extract, Analyze, etc.)
├── chunking.py      # Text chunking strategies
└── tools.py         # Custom tools (regex_extract, etc.)
```

## Code Style

- Python 3.10+, strict typing with `ty`
- Format with `ruff format`, lint with `ruff check`
- Follow existing patterns in surrounding code
- No hardcoded secrets—use Modal secrets or `.env`

## Testing

```bash
uv run pytest                              # All tests
uv run pytest tests/test_driver.py -v      # Single file
uv run pytest -k "test_name"               # Pattern match
```

Tests mock Modal—no cloud calls during `pytest`.

## Key Patterns

**Sandbox execution flow:**
1. `ModalInterpreter.start()` → creates Modal Sandbox
2. `execute(code)` → sends JSON to `sandbox_driver()`
3. Driver runs code, returns `{"stdout", "stderr", "final"}`
4. `SUBMIT(...)` in sandbox code signals final output

**Sandbox globals available to LLM-generated code:**
- `peek(text, start, length)` — slice text
- `grep(text, pattern)` — search lines
- `chunk_by_size(text, size)` — split text
- `chunk_by_headers(text, pattern)` — split at headers
- `add_buffer(name, value)` / `get_buffer(name)` — stateful accumulation
- `save_to_volume(path, content)` / `load_from_volume(path)` — persist to `/data/`
- `SUBMIT(*args, **kwargs)` — return structured output

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Missing planner LM | Check `.env` has `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` |
| Modal auth error | Run `uv run modal token set` |
| Import shadows modal | Delete any local `modal.py` file |
| Volume not found | Run `uv run modal volume create rlm-volume-dspy` |

## Documentation

- `README.md` — Full setup guide and CLI reference
- `docs/` — Additional architecture docs
- `notebooks/rlm-dspy-modal.ipynb` — Interactive tutorial
