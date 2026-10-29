"""Native DSPy parsing with temporary compatibility for recorded gateway formats.

Owner: runtime integration maintainers. Fixture contract and removal conditions:
`docs/runbooks/native-dspy-integration.md`; `gateway_adapter_replies.json`.
"""

from __future__ import annotations

import re

from dspy import AdapterParseError, ChatAdapter, Signature

_RAW_PYTHON_FENCE = re.compile(
    r"\s*(?P<reasoning>.*?)```python[ \t]*\r?\n(?P<code>.*?)\r?\n```[ \t]*"
    r"\s*(?:\[\[ ## completed ## \]\]\s*)?",
    re.DOTALL,
)


class FleetRLMChatAdapter(ChatAdapter):
    """Use native parsing/recovery first, with two fixture-backed parse-only exceptions.

    Native JSON fallback remains enabled. No Fleet retry, signature mutation,
    or preprocessing of a valid native response is performed.
    """

    def parse(self, signature: type[Signature], completion: str) -> dict[str, object]:
        """Accept recorded raw Python fences or an inline leaked-reasoning boundary."""
        try:
            return super().parse(signature, completion)
        except AdapterParseError:
            if set(signature.output_fields) != {"reasoning", "code"} or any(
                field.annotation is not str for field in signature.output_fields.values()
            ):
                raise
            # Only after native rejection: put the recorded inline action header on
            # its own line. Preserve the delimiter and all field text verbatim.
            separated = re.sub(r"</think>(?=\[\[ ## (?:reasoning|code) ## \]\])", "</think>\n", completion)
            if separated != completion:
                return super().parse(signature, separated)
            match = _RAW_PYTHON_FENCE.fullmatch(completion)
            if (
                match
                and match.group("code").strip()
                and "```" not in match.group("reasoning") + match.group("code")
                and "[[ ##" not in match.group("reasoning")
            ):
                return {"reasoning": match.group("reasoning").strip(), "code": match.group("code").strip()}
            raise
