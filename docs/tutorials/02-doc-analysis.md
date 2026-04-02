# Tutorial 02: Document Analysis

This tutorial shows the maintained long-context workflow via Python API.

## Goal

Analyze a document using the `run_long_context` runner and DSPy signatures.

## Analyze Mode

```bash
uv run python - <<'PY'
from fleet_rlm.cli.runners import run_long_context

result = run_long_context(
    docs_path="README.md",
    query="List the main architectural components",
    mode="analyze",
)
print("Answer:\n", result["answer"])
print("Findings count:", len(result.get("findings", [])))
PY
```

## Summarize Mode

```bash
uv run python - <<'PY'
from fleet_rlm.cli.runners import run_long_context

result = run_long_context(
    docs_path="README.md",
    query="Quick onboarding summary",
    mode="summarize",
)
print(result["summary"])
PY
```

## What This Uses

- `fleet_rlm.cli.runners.run_long_context`
- `SummarizeLongDocument`
- Modal sandbox helper stack (`peek`, `grep`, chunking helpers, buffer tools)

## Validation

If analysis fails:

1. verify planner LM env vars
2. verify Modal credentials and secret
3. retry with a smaller document first
