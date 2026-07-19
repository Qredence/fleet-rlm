"""Contracts for Fleet's pinned DSPy interpreter inject seam."""

from __future__ import annotations


def test_wrap_and_is_final_output_round_trip() -> None:
    from fleet_rlm.rlm.dspy_interpreter_contract import (
        FinalOutput,
        is_final_output,
        wrap_final_output,
    )

    wrapped = wrap_final_output({"answer": "ok"})
    assert isinstance(wrapped, FinalOutput)
    assert wrapped.output == {"answer": "ok"}
    assert is_final_output(wrapped)
    assert not is_final_output("stdout")


def test_copy_output_fields_defensive_copy() -> None:
    from fleet_rlm.rlm.dspy_interpreter_contract import copy_output_fields

    fields = [{"name": "answer", "type": "str"}]
    copied = copy_output_fields(fields)
    assert copied == fields
    assert copied is not fields
    assert copy_output_fields(None) is None


def test_needs_tool_reinjection_matches_inject_cycle() -> None:
    from fleet_rlm.rlm.dspy_interpreter_contract import (
        initial_tools_registered,
        mark_tools_registered,
        needs_tool_reinjection,
    )

    assert initial_tools_registered() is False
    assert mark_tools_registered() is True
    assert needs_tool_reinjection(tools_registered=False, http_broker_ready=False) is True
    assert needs_tool_reinjection(tools_registered=False, http_broker_ready=True) is True
    assert needs_tool_reinjection(tools_registered=True, http_broker_ready=False) is True
    assert needs_tool_reinjection(tools_registered=True, http_broker_ready=True) is False


def test_public_final_output_label_is_stable() -> None:
    from fleet_rlm.rlm.dspy_interpreter_contract import PUBLIC_FINAL_OUTPUT_LABEL

    assert PUBLIC_FINAL_OUTPUT_LABEL == "FINAL submitted"
