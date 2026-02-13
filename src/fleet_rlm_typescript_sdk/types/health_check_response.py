# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from typing import Optional

from .._models import BaseModel

__all__ = ["HealthCheckResponse"]


class HealthCheckResponse(BaseModel):
    ok: Optional[bool] = None

    version: Optional[str] = None
