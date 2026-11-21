---
name: data-analysis
description: Inspect CSV and tabular data, compute verified summaries, and save derived results.
---

Read inputs from the file manifest under `/workspace/files`. Inspect the header,
types, row count, and missing values before performing calculations. Use Python
or pandas for aggregation and verify totals against the input rows. State the
units and any exclusions alongside the result. Keep input files intact and
write requested derived files under `/workspace/artifacts`.
