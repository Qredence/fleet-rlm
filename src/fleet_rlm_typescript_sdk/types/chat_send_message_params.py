# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from __future__ import annotations

from typing import Optional
from typing_extensions import Required, TypedDict

__all__ = ["ChatSendMessageParams"]


class ChatSendMessageParams(TypedDict, total=False):
    message: Required[str]

    docs_path: Optional[str]

    trace: bool
