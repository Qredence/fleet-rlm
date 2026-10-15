"""Small adapters around DSPy's runtime-generated types and output formats."""

from __future__ import annotations

import re

from dspy.adapters.chat_adapter import ChatAdapter
from dspy.experimental import Choice
from dspy.signatures.signature import Signature
from pydantic import BaseModel

_RAW_PYTHON_FENCE = re.compile(
    r"\s*(?P<reasoning>.*?)```(?:python|py|python3|py3)?[ \t]*\r?\n(?P<code>.*?)\r?\n```[ \t]*\s*"
    r"(?:\[\[ ## completed ## \]\]\s*)?",
    re.IGNORECASE | re.DOTALL,
)


class FleetRLMChatAdapter(ChatAdapter):
    """Accept DSPy's documented raw Python fence when an RLM omits field labels."""

    def parse(self, signature: type[Signature], completion: str) -> dict[str, object]:
        output_fields = signature.output_fields
        if set(output_fields) == {"reasoning", "code"} and all(
            field.annotation is str for field in output_fields.values()
        ):
            match = _RAW_PYTHON_FENCE.fullmatch(completion)
            if match and match.group("code").strip() and "```" not in match.group("reasoning") + match.group("code"):
                return {"reasoning": match.group("reasoning").strip(), "code": match.group("code").strip()}
        return super().parse(signature, completion)


def make_choice_type(options: tuple[tuple[str, str], ...]) -> type[BaseModel]:
    """Build a validated DSPy Choice model from runtime options."""
    specialize = getattr(Choice, "__class_getitem__", None)
    if not callable(specialize):
        raise RuntimeError("This DSPy version cannot construct typed Choice options")
    result = specialize(options)
    if not isinstance(result, type) or not issubclass(result, BaseModel):
        raise RuntimeError("DSPy returned an invalid typed Choice model")
    return result
