# rlm-dspy-modal

Python package and Typer CLI extracted from `rlm-dspy-modal.ipynb`.

## Setup

```bash
# from repo root
cd rlm_content
uv sync
```

## CLI

```bash
uv run rlm-modal --help
uv run rlm-modal check-secret
uv run rlm-modal run-basic --question "What are the first 12 Fibonacci numbers?"
uv run rlm-modal run-architecture --docs-path dspy-doc/dspy-doc.txt --query "Extract all modules and optimizers"
```

## Tests

```bash
uv run pytest
```
