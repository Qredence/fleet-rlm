"""Scoring utilities for PDF needle-in-haystack retrieval evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

Verdict = Literal["PASS", "PARTIAL", "FAIL", "ABSTAIN_PASS", "ABSTAIN_FAIL", "N/A"]

_ABSTENTION_PHRASES = (
    "not found",
    "no evidence",
    "cannot find",
    "could not find",
    "can't find",
    "couldn't find",
    "does not appear",
    "doesn't appear",
    "not in the",
    "not present",
    "no mention",
    "unable to locate",
    "unable to find",
    "not included",
    "no quote",
    "not available",
    "cannot verify",
    "can't verify",
    "no such",
    "fictional",
    "does not exist",
)


def normalize_text(value: str) -> str:
    """Lowercase, collapse whitespace, normalize smart quotes."""
    text = str(value or "")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"\s+", " ", text.strip().lower())
    return text


def token_overlap_ratio(expected: str, actual: str) -> float:
    """Fraction of expected tokens present in order in actual (greedy)."""
    exp_tokens = normalize_text(expected).split()
    act_tokens = normalize_text(actual).split()
    if not exp_tokens:
        return 1.0
    if not act_tokens:
        return 0.0
    ei = 0
    matched = 0
    for token in act_tokens:
        if ei < len(exp_tokens) and token == exp_tokens[ei]:
            matched += 1
            ei += 1
    return matched / len(exp_tokens)


def coverage_score(answer: str, expected_contains: list[str]) -> tuple[float, list[str], list[str]]:
    """Score based on how many expected keywords appear in the answer."""
    if not expected_contains:
        return 1.0, [], []
    answer_lower = normalize_text(answer)
    found = [kw for kw in expected_contains if normalize_text(kw) in answer_lower]
    missing = [kw for kw in expected_contains if normalize_text(kw) not in answer_lower]
    score = len(found) / len(expected_contains)
    return score, found, missing


def routing_score(*, route: str | None, execution_mode: str = "auto", negative_control: bool = False) -> float:
    """1.0 when large-context RLM route is used for document needle tasks."""
    _ = negative_control
    route_norm = (route or "").strip().lower()
    mode_norm = execution_mode.strip().lower()
    if mode_norm in ("rlm", "rlm_only"):
        return 1.0
    if route_norm in ("large_context_rlm", "rlm", "url_document_rlm"):
        return 1.0
    if "large_context" in route_norm:
        return 1.0
    return 0.0


def quote_fidelity_score(
    answer: str,
    *,
    expected_exact_quote: str = "",
    expected_substrings: list[str] | None = None,
    forbidden_substrings: list[str] | None = None,
    negative_control: bool = False,
) -> tuple[float, Verdict, dict[str, Any]]:
    """Score quote accuracy for needle items or abstention for negatives."""
    details: dict[str, Any] = {}
    norm_answer = normalize_text(answer)

    for forbidden in forbidden_substrings or []:
        if normalize_text(forbidden) in norm_answer:
            details["forbidden_hit"] = forbidden
            return 0.0, "ABSTAIN_FAIL" if negative_control else "FAIL", details

    if negative_control:
        abstained = any(phrase in norm_answer for phrase in _ABSTENTION_PHRASES)
        details["abstention_detected"] = abstained
        return (1.0, "ABSTAIN_PASS", details) if abstained else (0.0, "ABSTAIN_FAIL", details)

    if expected_exact_quote:
        norm_expected = normalize_text(expected_exact_quote)
        if norm_expected in norm_answer:
            details["match_type"] = "exact_substring"
            return 1.0, "PASS", details
        overlap = token_overlap_ratio(expected_exact_quote, answer)
        details["token_overlap"] = overlap
        if overlap >= 0.9:
            details["match_type"] = "token_overlap"
            return 1.0, "PASS", details

    substrings = expected_substrings or []
    if substrings:
        cov, found, missing = coverage_score(answer, substrings)
        details["substring_coverage"] = cov
        details["found"] = found
        details["missing"] = missing
        if cov >= 1.0:
            return 0.75, "PARTIAL", details
        if cov >= 0.5:
            return 0.5, "PARTIAL", details

    return 0.0, "FAIL", details


def trajectory_grounding_score(
    trajectory: str | list[str] | None,
    *,
    require_document_text: bool = True,
) -> tuple[float, Verdict, dict[str, Any]]:
    """Score REPL trajectory for document-grounded inspection."""
    if trajectory is None:
        return 0.0, "FAIL", {"reason": "no_trajectory"}

    if isinstance(trajectory, list):
        blob = "\n".join(str(item) for item in trajectory)
    else:
        blob = str(trajectory)

    norm = blob.lower()
    details: dict[str, Any] = {}

    host_path_fail = bool(re.search(r"fitz\.open|pymupdf|/volumes/|/users/", norm) or re.search(r"open\(['\"]/", norm))
    details["host_path_access"] = host_path_fail

    grounded_patterns = (
        "document_text",
        ".extracted.txt",
        "manifest.json",
        "re.search",
        "re.findall",
        ".find(",
        "finditer",
    )
    grounded_hits = [pat for pat in grounded_patterns if pat.lower() in norm]
    details["grounded_hits"] = grounded_hits

    if host_path_fail and not grounded_hits:
        return 0.0, "FAIL", details
    if host_path_fail and grounded_hits:
        return 0.5, "PARTIAL", details

    if require_document_text and not grounded_hits:
        if len(norm.strip()) < 20:
            return 0.0, "FAIL", {**details, "reason": "empty_trajectory"}
        return 0.25, "PARTIAL", {**details, "reason": "no_document_inspection"}

    return 1.0, "PASS", details


@dataclass
class NeedleEvalScores:
    routing: float = 0.0
    quote: float = 0.0
    quote_verdict: Verdict = "N/A"
    abstention: float = 0.0
    abstention_verdict: Verdict = "N/A"
    trajectory: float = 0.0
    trajectory_verdict: Verdict = "N/A"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "routing": self.routing,
            "quote": self.quote,
            "quote_verdict": self.quote_verdict,
            "abstention": self.abstention,
            "abstention_verdict": self.abstention_verdict,
            "trajectory": self.trajectory,
            "trajectory_verdict": self.trajectory_verdict,
            "details": self.details,
        }


def score_needle_result(
    item: dict[str, Any],
    *,
    answer: str,
    route: str | None = None,
    execution_mode: str = "auto",
    trajectory: str | list[str] | None = None,
) -> NeedleEvalScores:
    """Aggregate per-item scores for harness and frontend logs."""
    negative = bool(item.get("negative_control"))
    routing = routing_score(route=route, execution_mode=execution_mode, negative_control=negative)

    quote_val, quote_verdict, quote_details = quote_fidelity_score(
        answer,
        expected_exact_quote=str(item.get("expected_exact_quote") or ""),
        expected_substrings=list(item.get("expected_substrings") or []),
        forbidden_substrings=list(item.get("forbidden_substrings") or []),
        negative_control=negative,
    )

    abstention = 0.0
    abstention_verdict: Verdict = "N/A"
    if negative:
        abstention = quote_val
        abstention_verdict = quote_verdict
        quote_val = 0.0
        quote_verdict = "N/A"

    traj_val, traj_verdict, traj_details = trajectory_grounding_score(trajectory)

    return NeedleEvalScores(
        routing=routing,
        quote=quote_val,
        quote_verdict=quote_verdict,
        abstention=abstention,
        abstention_verdict=abstention_verdict,
        trajectory=traj_val,
        trajectory_verdict=traj_verdict,
        details={
            "quote": quote_details,
            "trajectory": traj_details,
        },
    )


def aggregate_needle_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate metrics across evaluated items."""
    needle_items = [r for r in results if not r.get("item", {}).get("negative_control")]
    negative_items = [r for r in results if r.get("item", {}).get("negative_control")]

    def _mean(key: str, rows: list[dict[str, Any]]) -> float:
        vals = [float(r.get("scores", {}).get(key, 0) or 0) for r in rows if r.get("scores")]
        return sum(vals) / len(vals) if vals else 0.0

    def _pass_rate(verdict_key: str, rows: list[dict[str, Any]]) -> float:
        passes = sum(1 for r in rows if r.get("scores", {}).get(verdict_key) == "PASS")
        return passes / len(rows) if rows else 0.0

    pathless = [r for r in results if r.get("condition") == "pathless_followup"]
    pathless_fails = [r for r in pathless if r.get("verdict") == "FAIL"]

    return {
        "total_items": len(results),
        "needle_exact_accuracy": _pass_rate("quote_verdict", needle_items),
        "routing_accuracy": _mean("routing", results),
        "abstention_accuracy": _mean("abstention", negative_items),
        "trajectory_grounding_rate": _mean("trajectory", results),
        "followup_regression_rate": len(pathless_fails) / len(pathless) if pathless else 0.0,
        "mean_quote_score": _mean("quote", needle_items),
    }


__all__ = [
    "NeedleEvalScores",
    "aggregate_needle_results",
    "coverage_score",
    "normalize_text",
    "quote_fidelity_score",
    "routing_score",
    "score_needle_result",
    "token_overlap_ratio",
    "trajectory_grounding_score",
]
