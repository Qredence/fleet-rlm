"""Fleet's DSPy adapter: accept the documented raw Python fence and re-ask once on format failure."""

from __future__ import annotations

import re
from typing import Any

from dspy import AdapterParseError, ChatAdapter, Signature

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
