"""Root / sub / optional utility LM roles for one RLM turn."""

from __future__ import annotations

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
