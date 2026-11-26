---
name: data-analysis
description: Filter, group, reconcile, or summarize tabular records from CSV, Parquet, or spreadsheet exports. Use for derived tables and aggregate figures.
companion-skills: chart-visualization
---

Read inputs from `/workspace/files`. Inspect column types, units and missing values before choosing grouping keys. Decide whether a missing key forms its own group or excludes a row and report that choice when it affects the result.

Use pandas or PyArrow to compute the requested transformation. Reconcile included and excluded counts against the input and check group totals against an independent source total. Report the filters and units. Write requested derived tables under `/workspace/artifacts` and return their paths.
