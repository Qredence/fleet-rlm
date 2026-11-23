---
name: data-analysis
description: Use when profiling, filtering, grouping, or aggregating CSV, Parquet, or other tabular data, or exporting derived tables.
---

Read `/workspace/files` without modifying inputs. Inspect schema, row counts, units, and nulls. Track included and excluded rows, including missing group keys; make the null-key policy explicit. Reconcile counts and totals against the raw data using pandas or PyArrow. Report filters, units, and exclusions. Save requested derived tables in `/workspace/artifacts` and return their paths.
