---
name: tabular-analysis
description: Aggregate, filter, join, and compare CSV or JSON data in /workspace (totals, rates, group-bys, quarter or period comparisons, counterfactuals), write a JSON report and a verify script under artifacts/, and answer with figures taken from executed code.
---

# Tabular analysis

Use this for questions answered by computing over rows: sums, averages, rates,
group-bys, rankings, period-over-period changes, and "what if" recomputations.

Use the snapshot's installed Python packages and the task project's own dependency
manifest when one applies. Do not install packages into the shared snapshot during
a task. If a required dependency is unavailable, explain the limitation or ask
whether an isolated optional environment is acceptable.

## Steps

1. Inspect before computing. Print the header, the row count, a few rows, and
   the distinct values of the columns you will group or filter on:

   ```python
   import csv

   rows = list(csv.DictReader(open("/workspace/files/sales.csv", newline="")))
   print(len(rows), list(rows[0]))
   print(sorted({r["status"] for r in rows}))
   ```

2. Convert types explicitly (`int(...)`, `float(...)`) and state every filter in
   code, for example `status == "ok"`. Keep the result in variables such as
   `totals`, `rates` or `report`.
3. Print every figure you will report. Round only for display, for example
   `round(x, 2)` for money and `round(x, 4)` for rates.
4. Write results with `workspace.write_artifact("report.json", json.dumps(report, indent=2, sort_keys=True))`.
   It returns the path, which is relative to `artifacts/`.
5. When the task asks for verification, write an independent check (another
   filter path, or a standalone `verify_report.py` that reloads the CSV and
   compares it with the JSON), run it with `subprocess.run(["python", path])`,
   and require that it prints OK.
6. SUBMIT from variables, never from retyped numbers:

   ```python
   answer = f"Highest revenue: {top} (${totals[top]:,.2f})."
   SUBMIT(answer=answer, learned_skill_note=note)
   ```

   Fleet rejects a SUBMIT whose figures were typed into the code but never
   printed by an execution.

## Pitfalls

- Rates need an explicit denominator ("refunded / all orders in the region,
  any status" is not the same as "refunded / ok orders").
- Quarters are `(month - 1) // 3 + 1`.
- For a counterfactual ("if refunded orders had been ok"), add the refunded
  revenue to the computed base. Do not reuse a total from another filter.
- Prior-turn reports are under `artifacts/`. If one is missing in this
  sandbox, recompute from `files/` instead of recalling numbers.
