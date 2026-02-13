# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from .._models import BaseModel

__all__ = ["ReadyCheckResponse"]


class ReadyCheckResponse(BaseModel):
    planner_configured: bool

    ready: bool
