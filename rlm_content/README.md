# fleet-rlm

Python package and Typer CLI extracted from `notebooks/rlm-dspy-modal.ipynb`.

## Setup

```bash
# from repo root
uv sync
```

## CLI

```bash
uv run fleet-rlm --help
uv run fleet-rlm check-secret
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"
uv run fleet-rlm run-architecture --docs-path rlm_content/dspy-doc/dspy-doc.txt --query "Extract all modules and optimizers"
```

## Tests

```bash
uv run pytest
```
