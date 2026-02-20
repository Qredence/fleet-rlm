"""Base and shared Pydantic schemas for the FastAPI layer."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelCaseModel(BaseModel):
    """Base model that automatically aliases attributes from snake_case to camelCase.

    This ensures that Python snake_case variables serialize correctly for
    the frontend's expectations (e.g., created_at -> createdAt).
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
