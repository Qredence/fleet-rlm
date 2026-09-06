"""Fleet-owned output defaults, independent of native DSPy execution internals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from fleet_rlm.json_types import strict_json_dumps


@dataclass(frozen=True, slots=True)
class OutputField:
    name: str
    required: bool
    default_json: str | None = None


@dataclass(frozen=True, slots=True)
class FleetOutputContract:
    fields: tuple[OutputField, ...]

    @classmethod
    def from_signature(cls, signature: Any) -> FleetOutputContract:
        fields = []
        for name, field in signature.output_fields.items():
            default_json = None
            if not field.is_required():
                default = field.default
                if field.default_factory is not None:
                    default = cast(Callable[[], Any], field.default_factory)()
                default_json = strict_json_dumps(default)
            fields.append(OutputField(name, field.is_required(), default_json))
        return cls(tuple(fields))

    def merge(self, native_fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep native types while supplying Fleet's required/default metadata."""
        if [field.get("name") for field in native_fields] != [field.name for field in self.fields]:
            raise ValueError("interpreter output fields do not match the bound Fleet output contract")
        result = []
        for native, field in zip(native_fields, self.fields, strict=True):
            merged = {**native, "required": field.required}
            merged.pop("default_json", None)
            if field.default_json is not None:
                merged["default_json"] = field.default_json
            result.append(merged)
        return result


def bind_output_contract(interpreter: Any, signature: Any) -> None:
    """Bind through the caller-owned adapter; lightweight non-Fleet doubles opt out."""
    if signature is None or not hasattr(signature, "output_fields"):
        return
    bind = getattr(interpreter, "bind_output_contract", None)
    if callable(bind):
        bind(FleetOutputContract.from_signature(signature))
