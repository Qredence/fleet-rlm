"""Contracts for Fleet's pinned DSPy interpreter inject seam."""

from __future__ import annotations


def test_wrap_and_is_final_output_round_trip() -> None:
    from fleet_rlm.rlm.compat_3_3_1 import (
        FinalOutput,
        is_final_output,
        wrap_final_output,
    )

    wrapped = wrap_final_output({"answer": "ok"})
    assert isinstance(wrapped, FinalOutput)
    assert wrapped.output == {"answer": "ok"}
    assert is_final_output(wrapped)
    assert not is_final_output("stdout")


def test_interpreter_types_use_dspy_public_namespace() -> None:
    import dspy

    from fleet_rlm.rlm.compat_3_3_1 import (
        CodeInterpreter,
        FinalOutput,
    )

    assert CodeInterpreter is dspy.CodeInterpreter
    assert FinalOutput is dspy.FinalOutput


def test_copy_output_fields_defensive_copy() -> None:
    from fleet_rlm.rlm.compat_3_3_1 import copy_output_fields

    fields = [{"name": "answer", "type": "str"}]
    copied = copy_output_fields(fields)
    assert copied == fields
    assert copied is not fields
    assert copy_output_fields(None) is None


def test_copy_output_fields_does_not_share_nested_metadata() -> None:
    from fleet_rlm.rlm.compat_3_3_1 import copy_output_fields

    fields = [{"name": "answer", "metadata": {"description": "final answer"}}]
    copied = copy_output_fields(fields)

    assert copied is not None
    copied[0]["metadata"]["description"] = "changed"
    assert fields[0]["metadata"]["description"] == "final answer"


def test_needs_binding_refresh_uses_fleet_generation_state() -> None:
    from fleet_rlm.rlm.compat_3_3_1 import needs_binding_refresh

    assert needs_binding_refresh(desired_generation=1, installed_generation=0, broker_ready=False) is True
    assert needs_binding_refresh(desired_generation=1, installed_generation=0, broker_ready=True) is True
    assert needs_binding_refresh(desired_generation=1, installed_generation=1, broker_ready=False) is True
    assert needs_binding_refresh(desired_generation=1, installed_generation=1, broker_ready=True) is False


def test_public_final_output_label_is_stable() -> None:
    from fleet_rlm.rlm.compat_3_3_1 import PUBLIC_FINAL_OUTPUT_LABEL

    assert PUBLIC_FINAL_OUTPUT_LABEL == "FINAL submitted"
