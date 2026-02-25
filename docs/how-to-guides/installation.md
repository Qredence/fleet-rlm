# Installation Guide

This guide covers current installation paths for `fleet-rlm`.

## Prerequisites

- Python 3.10+
- `uv`
- Modal account (for sandbox execution)

## Option A: Install from PyPI

```bash
uv tool install fleet-rlm
fleet --help
```

Launch Web UI:

```bash
fleet web
```

## Option B: Install from Source (Contributors)

```bash
# from repo root
uv sync --extra dev --extra server --extra mcp
cp .env.example .env
```

Run help checks:

```bash
# from repo root
uv run fleet-rlm --help
uv run fleet --help
```

## Configure Planner LM

Set at least:

```ini
DSPY_LM_MODEL=openai/gpt-4o
DSPY_LLM_API_KEY=sk-...
```

## Configure Modal

See [Configuring Modal](configuring-modal.md).

## Verification (Current Workflows)

```bash
# Terminal chat surface
uv run fleet-rlm chat --trace-mode compact

# Web surface
uv run fleet web
```

Optional Python API verification:

```bash
uv run python - <<'PY'
from fleet_rlm.runners import run_long_context

result = run_long_context(
    docs_path="README.md",
    query="Summarize key architecture points",
    mode="analyze",
)
print(result["answer"][:200])
PY
```
