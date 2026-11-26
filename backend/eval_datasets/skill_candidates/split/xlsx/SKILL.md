---
name: xlsx
description: Create or update an Excel workbook, correct a formula, or deliver a template that recalculates when its inputs change.
---

For edits, open the original twice with openpyxl: in formula mode for writing, and data_only mode for inspecting cached results. Locate input cells, formula ranges, hidden rows, merged cells and cross-sheet references. Independently calculate the requested result from the intended source rows. A syntactically valid SUM can omit the last row.

Edit a copy, matching existing styles, sheets and layout. Use formulas for values that must respond to future edits. Save to /workspace/artifacts. Never save the data_only copy over the formula workbook.

If cached values are required, recalculate a copy with headless LibreOffice in a separate output directory and isolated user profile. Check the resulting formula, cached value, sheet names, formatting and hidden rows before handing it back. On a disposable copy, vary an input, recalculate and verify the expected change. Report an absent cache or unavailable recalculation explicitly.
