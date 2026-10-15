"""Small adapters around DSPy's runtime-generated types and output formats."""

from __future__ import annotations

import math
import re
from typing import Any

from dspy.adapters.chat_adapter import ChatAdapter
from dspy.experimental import Choice
from dspy.signatures.signature import Signature
from dspy.utils.exceptions import AdapterParseError
from pydantic import BaseModel


def without_mlflow_dspy_callback(callbacks: list[Any]) -> list[Any]:
    """TypeSafe uses manual evidence spans rather than MLflow's generative-LM hook."""
    return [
        callback
        for callback in callbacks
        if not (
            callback.__class__.__module__ == "mlflow.dspy.callback" and callback.__class__.__name__ == "MlflowCallback"
        )
    ]


def decision_evidence(decision: Any, lm: Any) -> dict[str, Any]:
    """Retain decision evidence without synthesizing absent confidence."""
    probabilities = getattr(decision, "probabilities", None)
    confidence = getattr(decision, "confidence", None)
    if not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
        confidence = None
    if isinstance(probabilities, dict):
        probabilities = {
            str(key): value
            for key, value in probabilities.items()
            if isinstance(value, (int, float)) and math.isfinite(value)
        }
    else:
        probabilities = None
    return {
        "raw_choice": getattr(decision, "value", decision if isinstance(decision, str) else None),
        "probabilities": probabilities,
        "confidence": confidence,
        "backend": "typesafe" if lm.__class__.__name__ == "TypeSafe" else "generative",
        "model": str(getattr(lm, "model", "unknown")),
    }


_RAW_PYTHON_FENCE = re.compile(
    r"\s*(?P<reasoning>.*?)```(?:python|py|python3|py3)?[ \t]*\r?\n(?P<code>.*?)\r?\n```[ \t]*\s*"
    r"(?:\[\[ ## completed ## \]\]\s*)?",
    re.IGNORECASE | re.DOTALL,
)


class FleetRLMChatAdapter(ChatAdapter):
    """Accept DSPy's documented raw Python fence and re-ask when a reply cannot be parsed.

    The re-ask lives in the adapter so it covers every predictor that runs under it: the primary RLM,
    spawned children, and the host-mediated predictors a sandbox program creates. The extra call goes
    through the same (metered) LM, so Turn budgets and deadlines still apply. Only format failures are
    retried; budget, deadline, and provider errors propagate.
    """

    def __init__(self, *args: Any, parse_retries: int = 1, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.parse_retries = parse_retries

    def __call__(self, lm: Any, lm_kwargs: Any, signature: Any, demos: Any, inputs: Any) -> Any:
        for attempt in range(self.parse_retries + 1):
            try:
                return super().__call__(lm, lm_kwargs, signature, demos, inputs)
            except AdapterParseError:
                if attempt == self.parse_retries:
                    raise
        raise AssertionError("unreachable")

    async def acall(self, lm: Any, lm_kwargs: Any, signature: Any, demos: Any, inputs: Any) -> Any:
        for attempt in range(self.parse_retries + 1):
            try:
                return await super().acall(lm, lm_kwargs, signature, demos, inputs)
            except AdapterParseError:
                if attempt == self.parse_retries:
                    raise
        raise AssertionError("unreachable")

    def parse(self, signature: type[Signature], completion: str) -> dict[str, object]:
        # Some providers leak the model's thinking into content, closed by </think>; the answer follows it.
        completion = completion.rsplit("</think>", 1)[-1]
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
