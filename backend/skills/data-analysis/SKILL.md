---
name: data-analysis
description: Filter, group, reconcile, or summarize tabular records from CSV, Parquet, or spreadsheet exports. Use when the question concerns the records, including totals computed from workbook rows.
---

Read inputs from `/workspace/files`. Inspect column types, units and missing values before choosing grouping keys. Decide whether a missing key forms its own group or excludes a row and report that choice when it affects the result.

Compute the transformation with pandas or PyArrow. Report the filters and units. Save requested derived tables under `/workspace/artifacts` and return their paths.

When a report asks for both grouped totals and a source total, compute the source total from the original amount column before filtering or dropping missing group keys, unless the caller explicitly defines a narrower source. Keep that value separate from the included subtotal. Partition excluded rows without double counting and verify both identities: included and excluded row counts sum to the input row count, and included and excluded amounts sum to the source total in the same units. Populate the reported source total from that original calculation.
