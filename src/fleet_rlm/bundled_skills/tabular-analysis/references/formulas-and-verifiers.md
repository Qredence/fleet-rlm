# Tabular Analysis Formulas and Verifiers

## Calendar Quarters

Compute calendar quarter (1–4) from a 1-indexed calendar month `1..12`:

```python
quarter = (month - 1) // 3 + 1
```

| Months | Quarter |
| --- | --- |
| 1, 2, 3 | Q1 (`1`) |
| 4, 5, 6 | Q2 (`2`) |
| 7, 8, 9 | Q3 (`3`) |
| 10, 11, 12 | Q4 (`4`) |

## Weighted Rate of Totals vs Mean of Rates

- For a pooled or denominator-weighted rate, compute the **rate of totals**:
  `sum(numerator) / sum(denominator)` (via `rate_of_totals(...)`). Use an
  unweighted mean of rates only when the task explicitly asks for that statistic.
- Always state the denominator population explicitly in code and in the answer
  (for example, `"refunded / all orders in region"` is distinct from
  `"refunded / ok orders in region"`).

## Decimal and `ROUND_HALF_UP` Formatting

- Accumulate currency and exact decimal columns with `decimal.Decimal`, not
  binary `float`, so summing `0.1 + 0.2` never drifts to `0.30000000000000004`.
- Do not round intermediate subtotals; round only final display figures using
  `ROUND_HALF_UP` (`Decimal.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)`),
  which avoids Python `round()`'s banker's rounding (`round(1.005, 2) -> 1.0`)
  and consistently rounds `.5` away from zero (`1.005 -> 1.01`).

## Counterfactual Recomputation

- For "what if" questions (such as `"if refunded orders had been ok"`), recompute
  from the explicit row filter or add the counterfactual slice directly to the
  computed base variable. Never reuse an unrelated filtered total.

## Independent Verification Before `SUBMIT`

1. Recompute via an independent filter or formula path, or verify serialized
   outputs with `verify_report_numbers(report_path, expected_numbers, required_phrases=...)`.
2. Print key figures for inspection and submit computed variables. Fleet checks
   numeric literals against observed inputs and execution output; a computed value
   submitted from a variable does not itself require a prior print.
