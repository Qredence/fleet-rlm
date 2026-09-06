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
        """
        Build an output contract from a signature's output fields.
        
        Parameters:
            signature (Any): Signature containing output field definitions.
        
        Returns:
            FleetOutputContract: Contract containing field requirements and JSON-encoded defaults.
        """
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
        """Merge Fleet-required and default metadata into native output fields while preserving their other metadata.
        
        Parameters:
        	native_fields (list[dict[str, Any]]): Native interpreter output field metadata whose names and order must match the Fleet contract.
        
        Returns:
        	list[dict[str, Any]]: Output field metadata with Fleet-required and default values applied.
        
        Raises:
        	ValueError: If the native fields do not match the Fleet contract in name or order.
        """
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
    """Bind the signature's output contract through a compatible interpreter adapter."""
    if signature is None or not hasattr(signature, "output_fields"):
        return
    bind = getattr(interpreter, "bind_output_contract", None)
    if callable(bind):
        bind(FleetOutputContract.from_signature(signature))
