# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from typing import Dict, Optional

from .._models import BaseModel

__all__ = ["TaskResponse"]


class TaskResponse(BaseModel):
    error: Optional[str] = None

    ok: Optional[bool] = None

    result: Optional[Dict[str, object]] = None
