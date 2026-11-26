---
name: xlsx
description: Create, edit, fix, or audit an Excel workbook, including formulas, hidden rows, missing caches and discrepancies between sheets.
---

## Inspect the source

Open an existing workbook twice with openpyxl. Use formula mode to inspect or edit formulas and `data_only=True` to read cached results. Identify the exact sheets, input cells, merged headers, hidden rows and cross-sheet ranges involved. Calculate the intended result from the source rows. A valid SUM can omit the last detail row.

For an audit, report each discrepancy with its range, included rows, expected total and observed result. Mark an absent cache as unverified. Preserve the input workbook.

## Create or edit

Work on a copy or create a new workbook. Match the template's sheet names, styles and layout. Write formulas for quantities that must change when the user edits inputs. Save the formula workbook under `/workspace/artifacts`. Saving the cached-value copy can replace formulas with values.

## Verify the deliverable

When cached values are required, recalculate a copy with headless LibreOffice using an isolated user profile and a separate output directory. Reopen the result in both modes. Check the formulas and cached results against the independent totals, and confirm that unrelated sheets, layout and hidden rows survived. Change an input on a disposable copy, recalculate, and compare the new result with the expected change. Return the verified workbook path and identify any calculation the available engine could not verify.
