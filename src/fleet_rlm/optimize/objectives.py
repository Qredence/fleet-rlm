"""Pure scoring objectives: no MLflow, no LM calls, no filesystem.

Every scorer sink (`scorers.py`) and the GEPA metric derive their numbers from here, so a
score can be recomputed and unit-tested without a live service.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from fleet_rlm.contracts import parse_route
from fleet_rlm.execution_evidence import MAX_ARTIFACT_BYTES, validate_structured

# Each metric returns (score, detail), or None when it does not apply to the example.
Metric = tuple[float, str] | None

# Routing-budget gauge constants. The first bound is the direct-path allowance; the second is
# the bounded-execution allowance; past it, each extra unit costs the same 0.02.
_DIRECT_BUDGET_UNITS = 2
_BOUNDED_BUDGET_UNITS = 15
_BOUNDED_ROUTE_SCORE = 0.95
_UNIT_PENALTY = 0.02
_ROUTE_FLOOR = 0.2


def efficiency(lm_calls: int, sandbox_errors: int, submit_rejections: int) -> float:
    """Budget units beyond three cost 0.015; each remote error or blocking rejection costs 0.15."""
    return round(max(0.0, 1.0 - 0.015 * max(0, lm_calls - 3) - 0.15 * (sandbox_errors + submit_rejections)), 3)


_NUMBER = r"[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_NUMERIC_TOKEN = re.compile(rf"(?<![\w.+-]){_NUMBER}(?![\w.]|,\d)")


def missing_values(answer: str, expected: list[Any]) -> list[Any]:
    """Match complete signed decimal/exponent tokens, with optional grouped thousands.

    Numeric equality is exact (Decimal; no tolerance). Text expectations retain
    case-insensitive word matching and comma removal, independently of numbers.
    """
    numbers = {Decimal(match.group().replace(",", "")) for match in _NUMERIC_TOKEN.finditer(answer)}
    text = answer.lower().replace(",", "")
    missing = []
    for value in expected:
        literal = str(value)
        if re.fullmatch(_NUMBER, literal):
            present = Decimal(literal.replace(",", "")) in numbers
        else:
            present = re.search(rf"(?<![\w.]){re.escape(literal.lower())}(?![\w])", text) is not None
        if not present:
            missing.append(value)
    return missing


def expected_values_metric(gold: Any, answer: str) -> Metric:
    """Every labelled gold value appears in the answer."""
    expected = getattr(gold, "expected_values", None)
    if not expected:
        return None
    missing = missing_values(answer, list(expected))
    return (0.0, f"missing {missing}") if missing else (1.0, "all present")


def route_metric(gold: Any, pred: Any) -> Metric:
    """The Turn took the labelled gold route."""
    expected = parse_route(getattr(gold, "route", None))
    if expected is None:
        return None
    actual = parse_route(getattr(pred, "route", None))
    return float(actual == expected), f"expected {expected.value}, took {actual and actual.value}"


def efficiency_metric(pred: Any) -> Metric:
    """Explicit host counters are required; legacy missing rejection counts cannot mean zero."""
    lm_calls = getattr(pred, "lm_calls", None)
    sandbox_errors = getattr(pred, "sandbox_errors", None)
    submit_rejections = getattr(pred, "submit_rejections", None)
    if type(lm_calls) is not int or type(sandbox_errors) is not int or type(submit_rejections) is not int:
        return None
    if any(value < 0 for value in (lm_calls, sandbox_errors, submit_rejections)):
        return None
    return efficiency(lm_calls, sandbox_errors, submit_rejections), (
        f"{lm_calls} budget units, {sandbox_errors} remote Python errors, {submit_rejections} submission rejections"
    )


def routing_efficiency(used_rlm_or_flex: bool, lm_calls: int) -> tuple[float, str]:
    """Score the route's budget use, monotonically in lm_calls for both route classes.

    A direct Turn is expected to stay inside the small allowance; a Flex/RLM Turn is allowed
    the bounded allowance. Past either bound, every extra unit costs the same penalty.
    """
    if used_rlm_or_flex:
        excess = max(0, lm_calls - _BOUNDED_BUDGET_UNITS)
        score = _BOUNDED_ROUTE_SCORE - _UNIT_PENALTY * excess
        detail = (
            f"Bounded RLM/Flex execution used {lm_calls} budget units."
            if not excess
            else f"Heavy route used {lm_calls} budget units."
        )
    else:
        excess = max(0, lm_calls - _DIRECT_BUDGET_UNITS)
        score = 1.0 - _UNIT_PENALTY * excess
        detail = (
            f"Fast path completed in {lm_calls} budget units."
            if not excess
            else f"Direct route used {lm_calls} budget units."
        )
    return round(max(_ROUTE_FLOOR, score), 3), detail


def _equal(actual: Any, expected: Any, tolerance: float, *, csv_numbers: bool = False) -> bool:
    """Match expected dictionary subsets and ordered lists using absolute numeric tolerance.

    Extra dictionary keys are allowed; list lengths must match. csv_numbers permits
    numeric strings for numeric expectations, but booleans never match numbers.
    """
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _equal(actual[key], value, tolerance, csv_numbers=csv_numbers)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_equal(a, b, tolerance, csv_numbers=csv_numbers) for a, b in zip(actual, expected, strict=True))
        )
    if type(expected) in (int, float):
        if isinstance(actual, bool):
            return False
        if not csv_numbers and type(actual) not in (int, float):
            return False
        try:
            return math.isfinite(float(actual)) and abs(float(actual) - expected) <= tolerance
        except (TypeError, ValueError):
            return False
    return type(actual) is type(expected) and actual == expected


def _file(receipt: dict[str, Any]) -> bytes:
    """Decode receipt bytes and verify their size and SHA-256 digest.

    Raise ValueError for bytes over MAX_ARTIFACT_BYTES or a digest mismatch;
    base64 decoding and missing field errors also propagate.
    """
    raw = base64.b64decode(receipt["base64"], validate=True) if "base64" in receipt else receipt["text"].encode("utf-8")
    if len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
        raise ValueError("Invalid artifact digest or size")
    return raw


def artifact_correctness(gold: dict[str, Any], evidence: dict[str, Any]) -> Metric:
    """Score declared artifacts as (score, reason), or return None when none are declared."""
    specifications = validate_structured(gold).get("artifacts", [])
    if not specifications:
        return None
    observed = evidence.get("artifacts", {})
    if observed.get("complete") is not True:
        return 0.0, "Required artifact evidence is missing or incomplete"
    for item in specifications:
        path, kind = item["path"], item["kind"]
        try:
            receipt = observed["files"][path]
            raw = _file(receipt)
            text = raw.decode("utf-8")
            if kind == "text":
                passed = text == item["expected"]
            elif kind == "json":
                passed = _equal(json.loads(text), item["expected"], item["tolerance"])
            elif kind == "csv":
                rows = list(csv.DictReader(io.StringIO(text)))
                keys = item["key_columns"]
                actual = {tuple(row[key] for key in keys): row for row in rows}
                wanted = {tuple(str(row[key]) for key in keys): row for row in item["expected"]}
                passed = (
                    len(actual) == len(rows) and len(wanted) == len(item["expected"]) and set(actual) == set(wanted)
                )
                passed = passed and all(
                    _equal(actual[key], row, item["tolerance"], csv_numbers=True) for key, row in wanted.items()
                )
            else:
                verification = evidence.get("python", {}).get(path, {})
                passed = (
                    verification.get("complete") is True
                    and verification.get("sha256") == receipt["sha256"]
                    and verification.get("exit_code") == item["exit_code"]
                    and (item["stdout"] is None or verification.get("stdout") == item["stdout"])
                )
            if not passed:
                return 0.0, f"Artifact check failed: {path}"
        except (KeyError, TypeError, ValueError, UnicodeError):
            return 0.0, f"Artifact evidence unavailable or invalid: {path}"
    return 1.0, "Every declared artifact passed"


def delegation_correctness(gold: dict[str, Any], evidence: dict[str, Any]) -> Metric:
    """Score required children as (score, reason), or return None when none are required.

    Each required child must match one unused successful child by inputs, artifacts, and promoted digests.
    """
    required = validate_structured(gold).get("children")
    if required is None:
        return None
    actual = evidence.get("children")
    if evidence.get("children_complete") is not True or not isinstance(actual, list) or len(actual) != len(required):
        return 0.0, "Child count or lifecycle evidence does not match"
    remaining = list(actual)
    files = evidence.get("artifacts", {}).get("files", {})
    for expectation in required:
        found = None
        for index, child in enumerate(remaining):
            if child.get("status") != "succeeded" or set(child.get("inputs", {})) != set(expectation["input_paths"]):
                continue
            receipts = child.get("artifacts", [])
            suffixes = {row["path"].split("/", 2)[-1] for row in receipts}
            if not set(expectation["artifacts"]) <= suffixes:
                continue
            if any(files.get(row.get("root_path", row["path"]), {}).get("sha256") != row["sha256"] for row in receipts):
                continue
            found = index
            break
        if found is None:
            return 0.0, "Child inputs, success, or promoted artifact digest did not match"
        remaining.pop(found)
    return 1.0, "Every declared child and promotion receipt passed"


def aggregate(applied: Mapping[str, tuple[float, str]]) -> tuple[float, float]:
    """Composite score and quality: a wrong answer always scores zero, efficiency only breaks ties."""
    correctness = min(value for name, (value, _) in applied.items() if name != "efficiency")
    efficiency_value = applied["efficiency"][0] if "efficiency" in applied else 0.0
    score = 0.9 + 0.1 * efficiency_value if correctness == 1 else 0.0
    return score, correctness
