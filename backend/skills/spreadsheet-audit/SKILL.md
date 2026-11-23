---
name: spreadsheet-audit
description: Use when auditing an Excel workbook for formulas, cached values, hidden rows, merged cells, or cross-sheet discrepancies.
---

Read `.xlsx` with `openpyxl.load_workbook` in both formula and `data_only` modes. Inspect sheets, headers, merged ranges, and hidden rows. Keep formulas separate from caches and report missing caches as unverified. Reconcile explicit sheet ranges, state whether hidden rows were included, and explain discrepancies. Save requested exports in `/workspace/artifacts`.
