"""Small adapters around DSPy's runtime-generated types and output formats."""

from __future__ import annotations

import math
import re
from typing import Any

from dspy import ChatAdapter, Signature
from dspy.experimental import Choice
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
    r"\s*(?P<reasoning>.*?)```(?:python|py|python3|py3)?[ \t]*\r?\n(?P<code>.*?)\r?\n```[ \t]*"
    r"(?:\r?\n(?:[ \t]*\r?\n)*[ \t]*```[ \t]*)?\s*"
    r"(?:\[\[ ## completed ## \]\]\s*)?",
    re.IGNORECASE | re.DOTALL,
)


class FleetRLMChatAdapter(ChatAdapter):
    """Accept DSPy's documented raw Python fence and re-ask when a reply cannot be parsed.

    The re-ask lives in the adapter so it covers every predictor that runs under it: the primary RLM,
    spawned children, and the host-mediated predictors a sandbox program creates. Fleet owns format
    recovery instead of stacking its retry over DSPy's JSON fallback. The extra call goes
    through the same (metered) LM, so Turn budgets and deadlines still apply. Only format failures are
    retried; budget, deadline, and provider errors propagate.
    """

    def __init__(
        self,
        callbacks: list[Any] | None = None,
        use_native_function_calling: bool = False,
        native_response_types: list[type[type]] | None = None,
        use_json_adapter_fallback: bool = False,
        parallel_tool_calls: bool | None = None,
        *,
        parse_retries: int = 1,
    ) -> None:
        """Configure the adapter; parse_retries must be 0 or 1 and JSON fallback must stay off."""
        if type(parse_retries) is not int or parse_retries not in (0, 1):
            raise ValueError("parse_retries must be 0 or 1")
        if use_json_adapter_fallback:
            raise ValueError("use_json_adapter_fallback must be False; Fleet owns format recovery")
        super().__init__(
            callbacks=callbacks,
            use_native_function_calling=use_native_function_calling,
            native_response_types=native_response_types,
            use_json_adapter_fallback=False,
            parallel_tool_calls=parallel_tool_calls,
        )
        self.parse_retries = parse_retries

    @staticmethod
    def _retry_signature(signature: type[Signature], error: AdapterParseError) -> type[Signature]:
        parsed = error.parsed_result if isinstance(error.parsed_result, dict) else {}
        fields = []
        for name, field in signature.output_fields.items():
            annotation = field.annotation
            type_name = getattr(annotation, "__name__", str(annotation))
            fields.append(f"[[ ## {name} ## ]] (type: {type_name})")
        missing = [name for name in signature.output_fields if name not in parsed]
        correction = (
            "Format correction: the previous reply could not be parsed. "
            f"Missing or invalid output fields: {', '.join(missing) or 'invalid field values'}. "
            f"Return every output field in order: {'; '.join(fields)}. "
            "Put each marker on its own line, followed by its value in the declared type. "
            "End with [[ ## completed ## ]]. Do not wrap the whole reply in a markdown fence "
            "or add prose outside the fields."
        )
        return signature.with_instructions(f"{signature.instructions}\n\n{correction}")

    def __call__(self, lm: Any, lm_kwargs: Any, signature: Any, demos: Any, inputs: Any) -> Any:
        """Run the predictor, re-asking once on format failures up to parse_retries."""
        for attempt in range(self.parse_retries + 1):
            try:
                return super().__call__(lm, dict(lm_kwargs), signature, demos, inputs)
            except AdapterParseError as error:
                if attempt == self.parse_retries:
                    raise
                signature = self._retry_signature(signature, error)
        raise AssertionError("unreachable")

    async def acall(self, lm: Any, lm_kwargs: Any, signature: Any, demos: Any, inputs: Any) -> Any:
        """Async variant of __call__ with the same format-retry behavior."""
        for attempt in range(self.parse_retries + 1):
            try:
                return await super().acall(lm, dict(lm_kwargs), signature, demos, inputs)
            except AdapterParseError as error:
                if attempt == self.parse_retries:
                    raise
                signature = self._retry_signature(signature, error)
        raise AssertionError("unreachable")

    def parse(self, signature: type[Signature], completion: str) -> dict[str, object]:
        """Parse a completion, dropping leaked thinking and accepting a raw Python fence for reasoning and code."""
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
