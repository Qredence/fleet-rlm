---
name: data-analysis
description: Inspect CSV, Parquet, and tabular datasets, compute verified summaries, and save derived tables.
---

Read inputs from `/workspace/files`. Inspect headers, dtypes, row counts, and missing values before computing aggregates. Use `pandas` or `pyarrow` for grouping and cross-check totals against raw input rows with an `assert`. State units, filters, and any excluded rows alongside the final answer. Never mutate input files; write derived tables to `/workspace/artifacts` and return their paths in `SUBMIT`.
