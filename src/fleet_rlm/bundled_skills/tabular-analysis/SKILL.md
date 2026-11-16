---
name: tabular-analysis
description: Profile and aggregate CSV, TSV, JSONL, or XLSX data and verify report figures. Use when totals, grouped comparisons, or rates must come from executed calculations.
---

# Tabular analysis

Use this for totals, averages, weighted rates, group-bys, rankings, period
comparisons, and counterfactuals over structured datasets. Use pre-installed
snapshot packages and do not install packages during a task.

## Steps

1. Load the module and profile the table first (schema, row count, distinct values).
   Helpers load rows into memory; bound inputs accordingly. XLSX reads the active
   sheet using `openpyxl` and cached formula values, without recalculation:


   ```python
   tab = load_skill_module("tabular-analysis")
   profile = tab.profile_table("/workspace/files/sales.csv")
   print(profile["row_count"], profile["columns"], profile["column_stats"])
   ```

2. Filter and aggregate with explicit columns and `Decimal` arithmetic:

   ```python
   agg = tab.aggregate_groups(
       "/workspace/files/sales.csv",
       group_cols=["region"],
       metric_cols=["revenue"],
       filters={"status": "ok"},
   )
   rate = tab.rate_of_totals(refunded_rows, "amount", all_rows, "amount", scale="percent", places=2)
   print(agg["totals"], agg["groups"], rate["formatted"])
   ```

3. Compute figures in variables and print key results for verification before
   `SUBMIT(...)`. Fleet checks numeric literals against observed inputs and
   execution output; computed values submitted from variables are also allowed.

4. Write JSON reports to `artifacts/` and verify them before submitting
   (`required_phrases` checks exact case-sensitive substrings in the written file):

   ```python
   report_path = workspace.write_artifact("report.json", json.dumps(report, indent=2, sort_keys=True))
   check = tab.verify_report_numbers(report_path, expected_numbers)
   print(check)
   assert check["ok"], check
   ```

5. Read `read_skill_reference("tabular-analysis", "references/formulas-and-verifiers.md")`
   for quarter math, weighted rates, counterfactual rules, and `ROUND_HALF_UP`.

6. `SUBMIT(answer=answer, learned_skill_note=note)` from computed variables. Prior
   reports live under `artifacts/`; recompute from `files/` if missing.
