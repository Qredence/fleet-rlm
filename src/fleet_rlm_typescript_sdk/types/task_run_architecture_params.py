# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from __future__ import annotations

from typing import Optional
from typing_extensions import Literal, Required, Annotated, TypedDict

from .._utils import PropertyInfo

__all__ = ["TaskRunArchitectureParams"]


class TaskRunArchitectureParams(TypedDict, total=False):
    task_type: Required[
        Literal["basic", "architecture", "api_endpoints", "error_patterns", "long_context", "summarize", "custom_tool"]
    ]

    chars: int

    docs_path: Optional[str]

    max_iterations: int

    max_llm_calls: int

    query: str

    question: str

    api_timeout: Annotated[int, PropertyInfo(alias="timeout")]

    verbose: bool
