"""Programmatic metrics for evaluating agent traces.

This module provides 6 synchronous, pure-Python metrics that compute
various aspects of trace quality without requiring LLM calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .trace_record import TraceRecord

# Metric names in canonical order
METRIC_NAMES = [
    "timeout_compliance",
    "trace_completeness",
    "token_cost",
    "latency_p95",
    "routing_correctness",
    "trajectory_redundancy",
]


def timeout_compliance(trace_record: TraceRecord) -> float:
    """Calculate timeout compliance ratio.

    Returns the ratio of spans that completed within their declared timeout
    to the total number of spans with a declared timeout.

    Args:
        trace_record: The trace to evaluate.

    Returns:
        Float in [0.0, 1.0]. Returns 1.0 if no spans declare timeouts.
    """
    spans_with_timeout = []

    for span in trace_record.trajectory_spans:
        # Check for timeout in span attributes
        timeout = span.attributes.get("timeout_ms", span.attributes.get("timeout"))
        if timeout is not None:
            duration_ms = (span.end - span.start) * 1000.0 if span.end > span.start else 0.0
            spans_with_timeout.append((duration_ms, float(timeout)))

    if not spans_with_timeout:
        # No spans declare timeouts, so compliance is perfect
        return 1.0

    compliant_count = sum(1 for duration, timeout in spans_with_timeout if duration <= timeout)
    return compliant_count / len(spans_with_timeout)


def trace_completeness(trace_record: TraceRecord) -> float:
    """Calculate trace completeness as average of three boolean flags.

    Checks:
    1. trace_outputs populated (non-empty dict)
    2. final_answer present (non-empty string)
    3. parent_span present (non-None)

    Args:
        trace_record: The trace to evaluate.

    Returns:
        Float in {0.0, 0.333..., 0.666..., 1.0}.
    """
    checks = [
        bool(trace_record.trace_outputs),  # trace_outputs populated
        bool(trace_record.final_answer.strip()),  # final_answer present
        trace_record.parent_span_id is not None,  # parent_span present
    ]

    return sum(checks) / len(checks)


def token_cost(trace_record: TraceRecord) -> float:
    """Calculate total token cost from all spans.

    Sums prompt_tokens + completion_tokens from span attributes.
    Falls back to legacy mlflow.traceInputTokens/mlflow.traceOutputTokens
    if GenAI attributes are absent.

    Args:
        trace_record: The trace to evaluate.

    Returns:
        Total token count as float. Returns 0.0 if no tokens found.
    """
    total_tokens = 0

    for span in trace_record.trajectory_spans:
        # Try GenAI attributes first
        prompt_tokens = span.attributes.get("gen_ai.usage.prompt_tokens")
        completion_tokens = span.attributes.get("gen_ai.usage.completion_tokens")

        # Fallback to legacy attributes
        if prompt_tokens is None:
            prompt_tokens = span.attributes.get("mlflow.traceInputTokens")
        if completion_tokens is None:
            completion_tokens = span.attributes.get("mlflow.traceOutputTokens")

        if isinstance(prompt_tokens, (int, float)):
            total_tokens += int(prompt_tokens)
        if isinstance(completion_tokens, (int, float)):
            total_tokens += int(completion_tokens)

    return float(total_tokens)


def latency_p95(trace_record: TraceRecord) -> float:
    """Calculate 95th percentile span latency in seconds.

    Args:
        trace_record: The trace to evaluate.

    Returns:
        P95 latency in seconds. Returns 0.0 if no spans.
    """
    if not trace_record.trajectory_spans:
        return 0.0

    # Calculate durations for all spans with valid start/end times
    durations = []
    for span in trace_record.trajectory_spans:
        if span.start >= 0 and span.end >= 0:
            duration = span.end - span.start
            if duration > 0:
                durations.append(duration)

    if not durations:
        return 0.0

    # Sort durations
    durations.sort()

    # Calculate p95 using nearest-rank method (ceiling)
    import math

    n = len(durations)
    rank = math.ceil(0.95 * n)
    if rank > n:
        rank = n
    # Convert to 0-indexed
    return durations[rank - 1]


def _infer_expected_route(user_request: str) -> str:
    """Infer expected route from user_request using heuristics.

    Args:
        user_request: The user's request text.

    Returns:
        Expected route: "rlm", "react", or "cot".
    """
    request_lower = user_request.lower()

    # RLM indicators: code generation, implementation, complex tasks
    rlm_keywords = [
        "code",
        "implement",
        "program",
        "function",
        "class",
        "algorithm",
        "build",
        "create",
        "develop",
        "write",
        "generate",
    ]
    if any(keyword in request_lower for keyword in rlm_keywords):
        return "rlm"

    # ReAct indicators: tool use, lookup, search
    react_keywords = [
        "search",
        "find",
        "lookup",
        "check",
        "fetch",
        "retrieve",
        "query",
        "get",
        "read",
        "browse",
    ]
    if any(keyword in request_lower for keyword in react_keywords):
        return "react"

    # CoT indicators: explanation, reasoning, simple questions
    cot_keywords = [
        "explain",
        "why",
        "how",
        "what",
        "describe",
        "tell me",
        "define",
        "compare",
        "analyze",
    ]
    if any(keyword in request_lower for keyword in cot_keywords):
        return "cot"

    # Default to CoT for conversational queries
    return "cot"


def routing_correctness(trace_record: TraceRecord) -> float:
    """Check if the executed route matches the expected route.

    Uses heuristics on user_request to infer the expected route and
    compares it to the actual route taken. When the route is "auto" or empty,
    falls back to active_skills to determine the actual route. Handles
    comma-separated route lists (e.g., "volume-bootstrap,optimization").

    Args:
        trace_record: The trace to evaluate.

    Returns:
        1.0 if routes match, 0.0 otherwise.
    """
    expected_route = _infer_expected_route(trace_record.user_request)
    actual_route = trace_record.route.lower()

    # Handle comma-separated routes (e.g., "volume-bootstrap,optimization")
    # Split and check if any route matches
    actual_routes = [r.strip() for r in actual_route.split(",") if r.strip()]

    # When route is "auto" or empty, derive from active_skills
    if not actual_routes or actual_route == "auto":
        skills = [s.lower() for s in trace_record.active_skills]
        if "rlm" in skills:
            actual_routes = ["rlm"]
        elif "react" in skills or "tools" in skills:
            actual_routes = ["react"]
        elif "cot" in skills or "chain_of_thought" in skills:
            actual_routes = ["cot"]
        else:
            actual_routes = []

    # Normalize route names - handle both exact matches and compound names
    route_aliases = {
        "rlm": ["rlm", "rlm_only", "large_context_rlm"],
        "react": ["react", "tools"],
        "cot": ["cot", "direct", "chain_of_thought"],
    }

    expected_normalized = expected_route.lower()

    # Check if any actual route matches expected route or its aliases
    for actual_route_lower in actual_routes:
        # Direct match
        if actual_route_lower == expected_normalized:
            return 1.0

        # Check against aliases
        if expected_normalized in route_aliases:
            if actual_route_lower in route_aliases[expected_normalized]:
                return 1.0

        # Check if the route contains the expected route as a substring
        # (e.g., "large_context_rlm" contains "rlm")
        if expected_normalized in actual_route_lower:
            return 1.0

    return 0.0


def _calculate_similarity(text1: str, text2: str) -> float:
    """Calculate Levenshtein similarity ratio between two strings.

    Uses dynamic-programming Levenshtein distance and converts to a
    similarity ratio in [0.0, 1.0].

    Args:
        text1: First string.
        text2: Second string.

    Returns:
        Similarity ratio in [0.0, 1.0]. 1.0 means identical.
    """
    if text1 == text2:
        return 1.0
    if not text1 or not text2:
        return 0.0

    len1, len2 = len(text1), len(text2)

    # Optimized Levenshtein distance using two rows (O(min(m,n)) space)
    if len1 < len2:
        text1, text2 = text2, text1
        len1, len2 = len2, len1

    prev = list(range(len2 + 1))
    curr = [0] * (len2 + 1)

    for i in range(1, len1 + 1):
        curr[0] = i
        for j in range(1, len2 + 1):
            cost = 0 if text1[i - 1] == text2[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,  # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev, curr = curr, prev

    distance = prev[len2]
    max_len = max(len1, len2)
    return 1.0 - (distance / max_len)


def trajectory_redundancy(trace_record: TraceRecord) -> float:
    """Count redundant tool calls within a 3-step window.

    A tool call is redundant if the same tool with similar input (>=0.8 similarity)
    was called within the previous 3 steps.

    Args:
        trace_record: The trace to evaluate.

    Returns:
        Count of redundant calls. Returns 0.0 if no redundancy.
    """
    if len(trace_record.trajectory_spans) < 2:
        return 0.0

    redundant_count = 0
    tool_calls = []

    # Extract tool calls with their inputs
    for span in trace_record.trajectory_spans:
        if span.tool_name:
            tool_input = str(span.tool_input) if span.tool_input else ""
            tool_calls.append((span.tool_name, tool_input))

    # Check for redundancy within 3-step window
    for i in range(len(tool_calls)):
        current_tool, current_input = tool_calls[i]

        # Look back up to 3 steps
        window_start = max(0, i - 3)
        for j in range(window_start, i):
            prev_tool, prev_input = tool_calls[j]

            # Check if same tool and similar input
            if current_tool == prev_tool:
                similarity = _calculate_similarity(current_input, prev_input)
                if similarity >= 0.8:
                    redundant_count += 1
                    break  # Count each call only once

    return float(redundant_count)


# Callable registry for all metrics
METRIC_CALLABLES = {
    "timeout_compliance": timeout_compliance,
    "trace_completeness": trace_completeness,
    "token_cost": token_cost,
    "latency_p95": latency_p95,
    "routing_correctness": routing_correctness,
    "trajectory_redundancy": trajectory_redundancy,
}
