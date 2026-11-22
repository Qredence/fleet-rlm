---
name: spreadsheet-audit
description: Audit multi-sheet Excel workbooks (.xlsx), inspect formulas and merged cells with openpyxl, and reconcile sheet totals.
---

Open `.xlsx` files from `/workspace/files` with `openpyxl.load_workbook()` (inspecting both `data_only=False` for formulas and `data_only=True` for cached values). Enumerate `sheetnames`, header offsets, merged ranges, and hidden rows before loading into `pandas`. Reconcile subtotals across sheets with `assert` checks, write cleaned exports to `/workspace/artifacts`, and report discrepancies in `SUBMIT`.
