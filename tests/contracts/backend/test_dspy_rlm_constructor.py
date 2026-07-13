"""Contract: installed dspy.RLM constructor surface used by RLMFactory."""

from __future__ import annotations

import inspect


def test_dspy_rlm_constructor_uses_max_iterations_not_max_iters() -> None:
    """Lock installed DSPy 3.3.Xb naming so upgrades fail here, not at runtime."""
    import dspy

    parameters = inspect.signature(dspy.RLM.__init__).parameters
    assert "max_iterations" in parameters
    assert "max_iters" not in parameters
    for name in (
        "signature",
        "max_llm_calls",
        "max_output_chars",
        "tools",
        "sub_lm",
        "interpreter",
    ):
        assert name in parameters, f"missing constructor field: {name}"


def test_dspy_rlm_accepts_file_tool_names_and_fresh_custom_interpreters() -> None:
    import dspy

    from fleet_rlm.rlm.signature import FleetRLMSignature

    class Interpreter:
        pass

    def read_attachment(attachment_id: str) -> dict[str, str]:
        return {"attachment_id": attachment_id}

    def create_artifact(kind: str, content: str, title: str | None = None) -> dict[str, str | None]:
        return {"kind": kind, "content": content, "title": title}

    first_interpreter = Interpreter()
    second_interpreter = Interpreter()
    first = dspy.RLM(
        FleetRLMSignature,
        tools=[read_attachment, create_artifact],
        interpreter=first_interpreter,
    )
    second = dspy.RLM(
        FleetRLMSignature,
        tools=[read_attachment, create_artifact],
        interpreter=second_interpreter,
    )

    assert set(first.tools) == {"read_attachment", "create_artifact"}
    assert set(second.tools) == {"read_attachment", "create_artifact"}
    assert first is not second
    assert first._interpreter is first_interpreter  # noqa: SLF001 - installed DSPy contract
    assert second._interpreter is second_interpreter  # noqa: SLF001 - installed DSPy contract
