"""Scoring helpers for the OOLONG-style benchmark tasks."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

_ANSWER_PREFIX = re.compile(r"^\s*(?:answer|result)\s*:\s*", re.IGNORECASE)
_DATE_LITERAL = re.compile(r"datetime\.date\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
_MARKDOWN = re.compile(r"\*\*(.*?)\*\*|__(.*?)__")


def attempt_answer_parse(answer: Any) -> str:
    """Normalize the common answer wrappers emitted by an RLM."""
    value = "" if answer is None else str(answer).strip()
    value = _ANSWER_PREFIX.sub("", value, count=1).strip()
    if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
        value = value[1:-1].strip()
    value = _MARKDOWN.sub(lambda match: match.group(1) or match.group(2) or "", value)
    return value.strip().strip("'").strip('"').strip()


def parse_gold(raw: Any) -> Any:
    """Parse the compact gold literals used by OOLONG task records."""
    text = str(raw).strip()
    date_match = _DATE_LITERAL.fullmatch(text.strip("[] "))
    if date_match:
        return datetime(
            int(date_match.group(1)),
            int(date_match.group(2)),
            int(date_match.group(3)),
        )
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text
    if isinstance(parsed, (list, tuple)) and len(parsed) == 1:
        return parsed[0]
    return parsed


def normalize_answer_type(answer_type: Any) -> str:
    """Keep only the two OOLONG types with special scoring semantics."""
    value = "" if answer_type is None else str(answer_type).strip().upper()
    if value.endswith("NUMERIC"):
        return "ANSWER_TYPE.NUMERIC"
    if value.endswith("DATE"):
        return "ANSWER_TYPE.DATE"
    return ""


def _parse_numeric(answer: str) -> int | float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", answer)
    if not match:
        return None
    try:
        value = float(match.group(0))
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _parse_date(answer: str) -> datetime | None:
    try:
        return datetime.strptime(answer, "%Y-%m-%d")
    except ValueError:
        return None


def score(gold: Any, answer_type: Any, answer: Any) -> float:
    """Score one answer using exact, date, and OOLONG numeric semantics."""
    parsed_gold = parse_gold(gold)
    parsed_answer = attempt_answer_parse(answer)
    if not parsed_answer:
        return 0.0

    normalized_type = normalize_answer_type(answer_type)
    if normalized_type == "ANSWER_TYPE.NUMERIC":
        expected = parsed_gold if isinstance(parsed_gold, (int, float)) else _parse_numeric(str(parsed_gold))
        predicted = _parse_numeric(parsed_answer)
        if expected is None or predicted is None:
            return 0.0
        return 0.75 ** abs(expected - predicted)
    if normalized_type == "ANSWER_TYPE.DATE":
        expected_date = parsed_gold if isinstance(parsed_gold, datetime) else _parse_date(str(parsed_gold))
        predicted_date = _parse_date(parsed_answer)
        return 1.0 if expected_date is not None and predicted_date == expected_date else 0.0

    expected_text = str(parsed_gold).strip().casefold()
    return 1.0 if parsed_answer.casefold() == expected_text else 0.0


def aggregate_scores(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Return bounded aggregate metrics for scored task records."""
    if not results:
        return {
            "tasks_total": 0,
            "avg_score": 0.0,
            "perfect_scores": 0,
            "partial_scores": 0,
            "zero_scores": 0,
            "by_answer_type": {},
            "by_context_len": {},
        }

    scores = [float(result.get("score", 0.0)) for result in results]
    answer_type_scores: dict[str, list[float]] = defaultdict(list)
    context_scores: dict[str, list[float]] = defaultdict(list)
    for result, value in zip(results, scores, strict=True):
        answer_type_scores[str(result.get("answer_type", ""))].append(value)
        context_scores[str(result.get("context_len", "unknown"))].append(value)

    def average(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "tasks_total": len(results),
        "avg_score": average(scores),
        "perfect_scores": sum(value >= 0.99 for value in scores),
        "partial_scores": sum(0.0 < value < 0.99 for value in scores),
        "zero_scores": sum(value == 0.0 for value in scores),
        "by_answer_type": {key: average(value) for key, value in sorted(answer_type_scores.items())},
        "by_context_len": {key: average(value) for key, value in sorted(context_scores.items())},
    }


__all__ = [
    "aggregate_scores",
    "attempt_answer_parse",
    "normalize_answer_type",
    "parse_gold",
    "score",
]
