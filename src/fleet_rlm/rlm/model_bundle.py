"""Root / sub / optional utility LM roles for one RLM turn."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from fleet_rlm.rlm.errors import RLMModelBundleError


@dataclass(frozen=True, slots=True)
class RLMModelBundle:
    """Server-owned model roles. Root steers; sub handles llm_query work."""

    root_lm: Any
    sub_lm: Any
    utility_lm: Any | None = None

    def __post_init__(self) -> None:
        if self.root_lm is None:
            raise RLMModelBundleError("root_lm is required")
        if self.sub_lm is None:
            raise RLMModelBundleError("sub_lm is required")

    def fork_for_child(self, *, deadline: float) -> RLMModelBundle:
        """Copy Root/Sub DSPy runtimes and bind every child LM call to one deadline."""
        return RLMModelBundle(
            root_lm=_copy_lm_for_child(self.root_lm, deadline=deadline),
            sub_lm=_copy_lm_for_child(self.sub_lm, deadline=deadline),
            utility_lm=self.utility_lm,
        )


def _copy_lm_for_child(lm: Any, *, deadline: float) -> Any:
    copy_lm = getattr(lm, "copy", None)
    if not callable(copy_lm):
        raise RLMModelBundleError("child LM must support DSPy runtime copy()")
    copied = copy_lm(num_retries=0)
    if copied is lm:
        raise RLMModelBundleError("child LM copy() must return an isolated runtime")

    original_forward = copied.forward

    def forward_with_deadline(*args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = _remaining_lm_timeout(deadline, copied, kwargs)
        return original_forward(*args, **kwargs)

    copied.forward = forward_with_deadline
    original_aforward = getattr(copied, "aforward", None)
    if callable(original_aforward):

        async def aforward_with_deadline(*args: Any, **kwargs: Any) -> Any:
            kwargs["timeout"] = _remaining_lm_timeout(deadline, copied, kwargs)
            return await original_aforward(*args, **kwargs)

        copied.aforward = aforward_with_deadline
    return copied


def _remaining_lm_timeout(deadline: float, lm: Any, call_kwargs: dict[str, Any]) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("recursive child LM deadline exceeded")
    configured = call_kwargs.get("timeout")
    if configured is None:
        configured = getattr(lm, "kwargs", {}).get("timeout")
    if isinstance(configured, (int, float)) and not isinstance(configured, bool) and configured > 0:
        return min(float(configured), remaining)
    return remaining
