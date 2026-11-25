---
name: spreadsheet-audit
description: Audit Excel workbook mechanics including formulas, cached values, hidden rows, merged ranges, and discrepancies between sheets.
---

Open the workbook with openpyxl in both formula and data_only modes. Identify the exact sheets and ranges behind each reported total. Match formulas to their cached values, marking absent caches as unverified.

Check how hidden rows, merged headers and cross-sheet references affect the calculation. Reconcile discrepancies against explicit ranges and state which rows were included. Preserve the input workbook and save requested exports under `/workspace/artifacts`.
