"""Profile, aggregate, rate-compute, and verify tabular datasets in CSV, TSV, JSONL, or XLSX format."""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_NUMBER_TOKEN_RE = re.compile(
    r"(?<![\w.+$%-])[+-]?(?:\$)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?%?(?!\w|\.\d|[+-]\d)"
)


def _load_rows_from_path(path: str | Path) -> tuple[str, list[str], list[dict[str, Any]], dict[str, Any]]:
    """Load tabular rows from CSV, TSV, JSONL, JSON, or XLSX without importing optional packages globally."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"Tabular dataset not found: {target}")
    suffix = target.suffix.lower()

    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            columns = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
        return suffix.lstrip("."), columns, rows, {}

    if suffix in {".jsonl", ".ndjson"}:
        rows = []
        columns_seen: dict[str, None] = {}
        with target.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                obj = json.loads(stripped)
                if not isinstance(obj, dict):
                    raise ValueError(f"Expected JSON object per line in {target}")
                for key in obj:
                    columns_seen.setdefault(str(key), None)
                rows.append(obj)
        return "jsonl", list(columns_seen), rows, {}

    if suffix == ".json":
        parsed = json.loads(target.read_text(encoding="utf-8-sig"))
        if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
            raise ValueError(f"JSON table {target} must be a list of row objects")
        columns_seen = {}
        for obj in parsed:
            for key in obj:
                columns_seen.setdefault(str(key), None)
        return "json", list(columns_seen), list(parsed), {}

    if suffix == ".xlsx":
        try:
            openpyxl = importlib.import_module("openpyxl")
        except ImportError as exc:
            raise RuntimeError("Reading .xlsx files requires openpyxl in the runtime environment") from exc
        workbook = openpyxl.load_workbook(target, read_only=True, data_only=True)
        try:
            sheet_names = list(workbook.sheetnames)
            sheet = workbook.active
            if sheet is None:
                return "xlsx", [], [], {"sheet_names": sheet_names, "active_sheet": None}
            iterator = sheet.iter_rows(values_only=True)
            header_row = next(iterator, None)
            if header_row is None:
                return "xlsx", [], [], {"sheet_names": sheet_names, "active_sheet": sheet.title}
            columns = [str(col) if col is not None else f"col_{idx}" for idx, col in enumerate(header_row)]
            rows = [
                {columns[idx]: (row[idx] if idx < len(row) else None) for idx in range(len(columns))}
                for row in iterator
                if any(cell is not None for cell in row)
            ]
            return "xlsx", columns, rows, {"sheet_names": sheet_names, "active_sheet": sheet.title}
        finally:
            workbook.close()

    raise ValueError(f"Unsupported tabular file format: {suffix or target.name!r}")


def _load_rows(rows_or_path: str | Path | Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a file path or row mapping sequence into a list of row dictionaries."""
    if isinstance(rows_or_path, (str, Path)):
        _, _, rows, _ = _load_rows_from_path(rows_or_path)
        return rows
    return [dict(row) for row in rows_or_path]


def _to_decimal(value: Any, column: str) -> Decimal:
    """Convert a cell value to Decimal, rejecting empty or non-numeric values clearly."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError(f"Boolean value in numeric column {column!r}: {value!r}")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if value is None:
        raise ValueError(f"Missing numeric value in column {column!r}")
    text = str(value).strip().replace(",", "")
    if text.startswith("$"):
        text = text[1:].strip()
    if not text:
        raise ValueError(f"Empty numeric value in column {column!r}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Non-numeric value in column {column!r}: {value!r}") from exc


def _to_float(value: Any, column: str) -> float:
    """Convert a cell value to float using the same validation as _to_decimal."""
    return float(_to_decimal(value, column))


def _matches_filters(
    row: Mapping[str, Any],
    filters: Mapping[str, Any] | Callable[[Mapping[str, Any]], bool] | None,
) -> bool:
    """Return True if a row satisfies mapping equality/membership or callable filters."""
    if filters is None:
        return True
    if callable(filters):
        return bool(filters(row))
    for key, expected in filters.items():
        actual = row.get(key)
        if isinstance(expected, (set, list, tuple, frozenset)):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _as_col_list(cols: Sequence[str] | str) -> list[str]:
    """Normalize a single column name or sequence of column names into a list."""
    return [cols] if isinstance(cols, str) else list(cols)


def profile_table(path: str | Path, *, sample_rows: int = 5) -> dict[str, Any]:
    """Inspect schema, row count, distinct column summaries, and sample rows from a tabular file."""
    if isinstance(sample_rows, bool) or not isinstance(sample_rows, int) or sample_rows < 0:
        raise ValueError("sample_rows must be a non-negative integer")
    fmt, columns, rows, extra = _load_rows_from_path(path)
    column_stats: dict[str, dict[str, Any]] = {}
    for col in columns:
        distinct: set[str] = set()
        non_empty = 0
        numeric_count = 0
        for row in rows:
            val = row.get(col)
            if val is not None and str(val).strip() != "":
                non_empty += 1
                distinct.add(str(val))
                try:
                    _to_decimal(val, col)
                    numeric_count += 1
                except ValueError:
                    # Non-numeric cells contribute to distinct/non-empty counts only.
                    pass
        column_stats[col] = {
            "non_empty_count": non_empty,
            "empty_count": len(rows) - non_empty,
            "distinct_count": len(distinct),
            "numeric_count": numeric_count,
            "sample_values": sorted(distinct)[:10],
        }
    return {
        "path": str(path),
        "format": fmt,
        "row_count": len(rows),
        "columns": columns,
        "column_stats": column_stats,
        "sample": rows[:sample_rows],
        **extra,
    }


def aggregate_groups(
    rows_or_path: str | Path | Sequence[Mapping[str, Any]],
    group_cols: Sequence[str] | str,
    metric_cols: Sequence[str] | str,
    *,
    use_decimal: bool = True,
    filters: Mapping[str, Any] | Callable[[Mapping[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Filter rows and compute grouped sums, means, mins, maxes, and grand totals."""
    group_list = _as_col_list(group_cols)
    metric_list = _as_col_list(metric_cols)
    if not metric_list:
        raise ValueError("metric_cols must contain at least one column name")

    rows = [row for row in _load_rows(rows_or_path) if _matches_filters(row, filters)]
    zero: Any = Decimal("0") if use_decimal else 0.0
    group_keys: dict[tuple[Any, ...], dict[str, Any]] = {}
    counts: dict[tuple[Any, ...], int] = {}
    sums: dict[tuple[Any, ...], dict[str, Any]] = {}
    mins: dict[tuple[Any, ...], dict[str, Any]] = {}
    maxs: dict[tuple[Any, ...], dict[str, Any]] = {}
    grand_totals: dict[str, Any] = {col: zero for col in metric_list}

    for row in rows:
        for col in group_list:
            if col not in row:
                raise KeyError(f"Group column {col!r} missing from row")
        key_tuple = tuple(row.get(col) for col in group_list)
        if key_tuple not in group_keys:
            group_keys[key_tuple] = {col: row.get(col) for col in group_list}
            counts[key_tuple] = 0
            sums[key_tuple] = {col: zero for col in metric_list}
            mins[key_tuple] = {col: None for col in metric_list}
            maxs[key_tuple] = {col: None for col in metric_list}
        counts[key_tuple] += 1
        row_sums = sums[key_tuple]
        row_mins = mins[key_tuple]
        row_maxs = maxs[key_tuple]
        for col in metric_list:
            if col not in row:
                raise KeyError(f"Metric column {col!r} missing from row")
            val: Any = _to_decimal(row[col], col) if use_decimal else _to_float(row[col], col)
            row_sums[col] += val
            grand_totals[col] += val
            cur_min = row_mins[col]
            cur_max = row_maxs[col]
            row_mins[col] = val if cur_min is None or val < cur_min else cur_min
            row_maxs[col] = val if cur_max is None or val > cur_max else cur_max

    groups = []
    for key_tuple in sorted(group_keys, key=lambda k: tuple("" if v is None else str(v) for v in k)):
        count = counts[key_tuple]
        row_sums = sums[key_tuple]
        row_mins = mins[key_tuple]
        row_maxs = maxs[key_tuple]
        groups.append(
            {
                "key": group_keys[key_tuple],
                "count": count,
                "sums": {col: float(row_sums[col]) for col in metric_list},
                "means": {col: float(row_sums[col] / count) for col in metric_list},
                "mins": {col: float(row_mins[col]) for col in metric_list},
                "maxs": {col: float(row_maxs[col]) for col in metric_list},
            }
        )

    return {
        "group_cols": group_list,
        "metric_cols": metric_list,
        "row_count": len(rows),
        "groups": groups,
        "totals": {col: float(grand_totals[col]) for col in metric_list},
    }


def rate_of_totals(
    numerator_rows: str | Path | Sequence[Mapping[str, Any]],
    num_col: str,
    denominator_rows: str | Path | Sequence[Mapping[str, Any]],
    den_col: str,
    *,
    scale: str = "percent",
    places: int = 2,
) -> dict[str, Any]:
    """Compute a weighted rate-of-totals with Decimal and ROUND_HALF_UP precision."""
    if scale not in {"percent", "ratio", "fraction"}:
        raise ValueError(f"Unsupported scale {scale!r}; expected 'percent' or 'ratio'")
    if isinstance(places, bool) or not isinstance(places, int) or places < 0:
        raise ValueError("places must be a non-negative integer")

    num_total = sum((_to_decimal(row.get(num_col), num_col) for row in _load_rows(numerator_rows)), Decimal("0"))
    den_total = sum((_to_decimal(row.get(den_col), den_col) for row in _load_rows(denominator_rows)), Decimal("0"))
    if den_total == 0:
        raise ZeroDivisionError("Denominator total is zero")

    multiplier = Decimal("100") if scale == "percent" else Decimal("1")
    raw_rate = (num_total / den_total) * multiplier
    quantizer = Decimal(1) if places == 0 else Decimal(10) ** -places
    quantized = raw_rate.quantize(quantizer, rounding=ROUND_HALF_UP)
    rate_str = f"{quantized:.{places}f}"
    suffix = "%" if scale == "percent" else ""
    return {
        "numerator": float(num_total),
        "denominator": float(den_total),
        "rate": float(quantized),
        "rate_str": rate_str,
        "formatted": f"{rate_str}{suffix}",
        "scale": scale,
        "places": places,
    }


def _parse_numeric_literal(raw: int | float | str | Decimal) -> float:
    """Normalize a numeric literal or formatted string into a float for report verification."""
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        value = float(raw)
    else:
        text = str(raw).strip().replace(",", "").lstrip("$").rstrip("%")
        value = float(text)
    if not math.isfinite(value):
        raise ValueError("Report numbers must be finite")
    return value


def verify_report_numbers(
    report_path: str | Path,
    expected_numbers: Mapping[str, int | float | str | Decimal] | Sequence[int | float | str | Decimal],
    *,
    required_phrases: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Verify expected numbers and required phrases against a generated report file."""
    target = Path(report_path)
    if not target.is_file():
        raise FileNotFoundError(f"Report file not found: {target}")
    text = target.read_text(encoding="utf-8")

    parsed_json: Any = None
    try:
        parsed_json = json.loads(text)
    except json.JSONDecodeError:
        parsed_json = None

    observed_numbers: list[float] = []
    for match in _NUMBER_TOKEN_RE.finditer(text):
        try:
            observed_numbers.append(_parse_numeric_literal(match.group(0)))
        except ValueError:
            continue

    items = (
        list(expected_numbers.items()) if isinstance(expected_numbers, Mapping) else list(enumerate(expected_numbers))
    )
    missing_numbers: list[dict[str, Any]] = []
    mismatched_keys: list[dict[str, Any]] = []

    for label, raw_expected in items:
        expected_val = _parse_numeric_literal(raw_expected)
        tol = max(1e-9, abs(expected_val) * 1e-12)
        if isinstance(expected_numbers, Mapping) and isinstance(parsed_json, Mapping):
            if str(label) not in parsed_json:
                missing_numbers.append({"label": str(label), "expected": expected_val})
                continue
            actual_raw = parsed_json[str(label)]
            try:
                actual_val = _parse_numeric_literal(actual_raw)
            except (TypeError, ValueError):
                mismatched_keys.append({"key": str(label), "expected": expected_val, "actual": actual_raw})
                continue
            if abs(actual_val - expected_val) > tol:
                mismatched_keys.append({"key": str(label), "expected": expected_val, "actual": actual_val})
            continue

        if not any(abs(seen - expected_val) <= tol for seen in observed_numbers):
            missing_numbers.append({"label": str(label), "expected": expected_val})

    missing_phrases = [phrase for phrase in (required_phrases or ()) if phrase not in text]
    ok = not missing_numbers and not mismatched_keys and not missing_phrases
    return {
        "ok": ok,
        "report_path": str(target),
        "checked_numbers": len(items),
        "checked_phrases": len(required_phrases or ()),
        "missing_numbers": missing_numbers,
        "mismatched_keys": mismatched_keys,
        "missing_phrases": missing_phrases,
    }


def run(
    path: str | Path,
    *,
    group_by: Sequence[str] | str | None = None,
    metrics: Sequence[str] | str | None = None,
) -> dict[str, Any]:
    """Profile a tabular dataset and optionally compute grouped aggregations."""
    profile = profile_table(path)
    result: dict[str, Any] = {"profile": profile}
    if group_by is not None and metrics is not None:
        result["aggregation"] = aggregate_groups(path, group_by, metrics)
    return result


def _json_default(value: Any) -> str:
    """Encode ISO date/time values rejected by the standard encoder."""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for profiling and aggregating tabular datasets."""
    parser = argparse.ArgumentParser(description="Profile and aggregate a tabular dataset.")
    parser.add_argument("path", help="Path to CSV, TSV, JSONL, JSON, or XLSX dataset")
    parser.add_argument("--group-by", nargs="+", default=None, help="Columns to group by")
    parser.add_argument("--metrics", nargs="+", default=None, help="Numeric metric columns to aggregate")
    parser.add_argument("--sample-rows", type=int, default=5, help="Number of sample rows to return (default: 5)")
    args = parser.parse_args(argv)

    try:
        profile = profile_table(args.path, sample_rows=args.sample_rows)
        output: dict[str, Any] = {"profile": profile}
        if args.group_by is not None:
            if not args.metrics:
                raise ValueError("--metrics is required when --group-by is specified")
            output["aggregation"] = aggregate_groups(args.path, args.group_by, args.metrics)
        sys.stdout.write(json.dumps(output, indent=2, default=_json_default) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
