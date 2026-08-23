"""Thread-safe internal delegation metrics for one Fleet RLM invocation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal, TypeAlias

# Closed observability contract: token totals are either truly observed from a
# provider/history entry or unavailable. There is no "estimated" state.
TokenUsageStatus: TypeAlias = Literal["observed", "unavailable"]

_TOKEN_USAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "total_tokens": ("total_tokens",),
    "cache_read_tokens": (
        "cache_read_tokens",
        "cache_read_input_tokens",
        "prompt_cache_hit_tokens",
    ),
    "cache_creation_tokens": ("cache_creation_tokens", "cache_creation_input_tokens"),
}


@dataclass(frozen=True, slots=True)
class DelegationMetricsSnapshot:
    """Bounded, content-free delegation measurements."""

    root_lm_calls_depth_0: int = 0
    sub_lm_calls_depth_0: int = 0
    child_root_lm_calls_depth_1: int = 0
    child_sub_lm_calls_depth_1: int = 0
    recursive_child_calls: int = 0
    recursive_batch_calls: int = 0
    recursive_children_started: int = 0
    recursive_children_completed: int = 0
    depth_fallback_calls: int = 0
    peak_child_concurrency: int = 0
    lm_call_counts: tuple[tuple[str, int, int], ...] = ()
    lm_latency_ms: tuple[tuple[str, int, float], ...] = ()
    # Entries are (role, recursive_depth, input_tokens, output_tokens, total_tokens);
    # input/output are kept alongside the total so partial usage never reads as 0.
    # Entries exist only for calls where usage was actually observed; a call
    # whose provider reported no usage must never emit an all-zero entry.
    lm_token_totals: tuple[tuple[str, int, int, int, int], ...] = ()
    token_usage_status: TokenUsageStatus = "unavailable"

    def as_dict(self) -> dict[str, object]:
        """
        Return a bounded JSON- and MLflow-compatible representation of the metrics snapshot.

        Returns:
            dict[str, object]: Serialized metrics, including call counts, latency
                totals rounded to three decimal places, observed token totals, and
                token usage status.
        """
        return {
            "root_lm_calls_depth_0": self.root_lm_calls_depth_0,
            "sub_lm_calls_depth_0": self.sub_lm_calls_depth_0,
            "child_root_lm_calls_depth_1": self.child_root_lm_calls_depth_1,
            "child_sub_lm_calls_depth_1": self.child_sub_lm_calls_depth_1,
            "recursive_child_calls": self.recursive_child_calls,
            "recursive_batch_calls": self.recursive_batch_calls,
            "recursive_children_started": self.recursive_children_started,
            "recursive_children_completed": self.recursive_children_completed,
            "depth_fallback_calls": self.depth_fallback_calls,
            "peak_child_concurrency": self.peak_child_concurrency,
            "lm_call_counts": [
                {"role": role, "recursive_depth": depth, "count": count} for role, depth, count in self.lm_call_counts
            ],
            "lm_latency_ms": [
                {"role": role, "recursive_depth": depth, "total_ms": round(total, 3)}
                for role, depth, total in self.lm_latency_ms
            ],
            "lm_token_totals": [
                {
                    "role": role,
                    "recursive_depth": depth,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "tokens": tokens,
                }
                for role, depth, input_tokens, output_tokens, tokens in self.lm_token_totals
            ],
            "token_usage_status": self.token_usage_status,
        }


class DelegationMetrics:
    """Accumulate role/depth and bounded recursive fan-out metrics safely."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._lm_calls: dict[tuple[str, int], int] = {}
        self._lm_latency_ms: dict[tuple[str, int], float] = {}
        self._lm_input_tokens: dict[tuple[str, int], int] = {}
        self._lm_output_tokens: dict[tuple[str, int], int] = {}
        self._lm_tokens: dict[tuple[str, int], int] = {}
        self._lm_usage_observed: set[tuple[str, int]] = set()
        self._recursive_child_calls = 0
        self._recursive_batch_calls = 0
        self._recursive_children_started = 0
        self._recursive_children_completed = 0
        self._depth_fallback_calls = 0
        self._active_children = 0
        self._peak_child_concurrency = 0

    def record_lm_call(
        self,
        role: str,
        recursive_depth: int,
        *,
        duration_ms: float = 0.0,
        usage: Mapping[str, Any] | None = None,
    ) -> None:
        """
        Record a language-model request and its aggregate metrics.

        Parameters:
            role (str): Model role, normalized to ``"root"``, ``"sub"``, or ``"unknown"``.
            recursive_depth (int): Recursion depth associated with the request.
            duration_ms (float): Request duration in milliseconds.
            usage (Mapping[str, Any] | None): Provider token-usage data, if available.
                Token totals are recorded only when usage is observed.
        """
        normalized_role = role if role in {"root", "sub"} else "unknown"
        key = (normalized_role, max(0, int(recursive_depth)))
        normalized_usage = normalize_lm_token_usage(usage)
        # Only an actually-observed usage mapping creates token buckets. Call
        # counts and latency stay unconditional; token totals remain absent
        # when the provider reported nothing, so zero can never masquerade as
        # a measurement.
        usage_observed = bool(normalized_usage)
        input_tokens = normalized_usage.get("input_tokens", 0)
        output_tokens = normalized_usage.get("output_tokens", 0)
        tokens = normalized_usage.get("total_tokens", 0)
        with self._lock:
            self._lm_calls[key] = self._lm_calls.get(key, 0) + 1
            self._lm_latency_ms[key] = self._lm_latency_ms.get(key, 0.0) + max(0.0, float(duration_ms))
            if usage_observed:
                self._lm_usage_observed.add(key)
                self._lm_input_tokens[key] = self._lm_input_tokens.get(key, 0) + input_tokens
                self._lm_output_tokens[key] = self._lm_output_tokens.get(key, 0) + output_tokens
                self._lm_tokens[key] = self._lm_tokens.get(key, 0) + tokens

    def record_recursive_call(self) -> None:
        """Record one recursive child call."""
        with self._lock:
            self._recursive_child_calls += 1

    def record_recursive_batch(self) -> None:
        with self._lock:
            self._recursive_batch_calls += 1

    def record_depth_fallback(self) -> None:
        with self._lock:
            self._depth_fallback_calls += 1

    def child_started(self) -> None:
        with self._lock:
            self._recursive_children_started += 1
            self._active_children += 1
            self._peak_child_concurrency = max(self._peak_child_concurrency, self._active_children)

    def child_completed(self) -> None:
        with self._lock:
            self._recursive_children_completed += 1
            self._active_children = max(0, self._active_children - 1)

    def snapshot(self) -> DelegationMetricsSnapshot:
        """Create an immutable snapshot of the accumulated delegation metrics.

        Returns:
            DelegationMetricsSnapshot: The current metrics, including call counts,
                latency totals, concurrency data, and token usage status.
        """
        with self._lock:
            calls = tuple(sorted((role, depth, count) for (role, depth), count in self._lm_calls.items()))
            latency = tuple(sorted((role, depth, total) for (role, depth), total in self._lm_latency_ms.items()))
            token_keys = self._lm_input_tokens.keys() | self._lm_output_tokens.keys() | self._lm_tokens.keys()
            tokens = tuple(
                sorted(
                    (
                        role,
                        depth,
                        self._lm_input_tokens.get((role, depth), 0),
                        self._lm_output_tokens.get((role, depth), 0),
                        self._lm_tokens.get((role, depth), 0),
                    )
                    for (role, depth) in token_keys
                )
            )
            return DelegationMetricsSnapshot(
                root_lm_calls_depth_0=self._lm_calls.get(("root", 0), 0),
                sub_lm_calls_depth_0=self._lm_calls.get(("sub", 0), 0),
                child_root_lm_calls_depth_1=self._lm_calls.get(("root", 1), 0),
                child_sub_lm_calls_depth_1=self._lm_calls.get(("sub", 1), 0),
                recursive_child_calls=self._recursive_child_calls,
                recursive_batch_calls=self._recursive_batch_calls,
                recursive_children_started=self._recursive_children_started,
                recursive_children_completed=self._recursive_children_completed,
                depth_fallback_calls=self._depth_fallback_calls,
                peak_child_concurrency=self._peak_child_concurrency,
                lm_call_counts=calls,
                lm_latency_ms=latency,
                lm_token_totals=tokens,
                token_usage_status="observed" if self._lm_usage_observed else "unavailable",
            )


def normalize_lm_token_usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    """
    Normalize provider token usage fields into canonical token names.

    Parameters:
        usage (Mapping[str, Any] | None): Provider usage data containing supported token field aliases.

    Returns:
        dict[str, int]: Canonical nonnegative token counts, with total tokens derived
            from input and output counts when unavailable.
    """
    if not isinstance(usage, Mapping):
        return {}
    normalized: dict[str, int] = {}
    for target, aliases in _TOKEN_USAGE_ALIASES.items():
        value = next(
            (
                candidate
                for alias in aliases
                if isinstance((candidate := usage.get(alias)), (int, float)) and not isinstance(candidate, bool)
            ),
            None,
        )
        if value is not None:
            normalized[target] = max(0, int(value))
    if "total_tokens" not in normalized and ("input_tokens" in normalized or "output_tokens" in normalized):
        normalized["total_tokens"] = normalized.get("input_tokens", 0) + normalized.get("output_tokens", 0)
    return normalized


__all__ = ["DelegationMetrics", "DelegationMetricsSnapshot", "TokenUsageStatus", "normalize_lm_token_usage"]
