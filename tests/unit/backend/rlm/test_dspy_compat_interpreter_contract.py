"""Contracts for Fleet's DSPy interpreter-facing helpers."""

from __future__ import annotations


def test_wrap_and_is_final_output_round_trip() -> None:
    from dspy import FinalOutput

    from fleet_rlm.daytona.interpreter import is_final_output, wrap_final_output

    wrapped = wrap_final_output({"answer": "ok"})
    assert isinstance(wrapped, FinalOutput)
    assert wrapped.output == {"answer": "ok"}
    assert is_final_output(wrapped)
    assert not is_final_output("stdout")


def test_interpreter_types_use_dspy_public_namespace() -> None:
    import dspy
    from dspy import CodeInterpreter, FinalOutput

    assert CodeInterpreter is dspy.CodeInterpreter
    assert FinalOutput is dspy.FinalOutput


def test_copy_output_fields_defensive_copy() -> None:
    from fleet_rlm.daytona.interpreter import copy_output_fields

    fields = [{"name": "answer", "type": "str"}]
    copied = copy_output_fields(fields)
    assert copied == fields
    assert copied is not fields
    assert copy_output_fields(None) is None


def test_copy_output_fields_does_not_share_nested_metadata() -> None:
    from fleet_rlm.daytona.interpreter import copy_output_fields

    fields = [{"name": "answer", "metadata": {"description": "final answer"}}]
    copied = copy_output_fields(fields)

    assert copied is not None
    copied[0]["metadata"]["description"] = "changed"
    assert fields[0]["metadata"]["description"] == "final answer"


def test_output_metadata_cannot_mutate_invocation_through_aliases() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    fields = [{"name": "answer", "type": "str", "metadata": {"description": "original"}}]
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), output_fields=fields)
    fields[0]["name"] = "changed"
    interp.execute("value = 41")
    snapshot = interp.output_fields
    assert snapshot is not None
    snapshot[0]["metadata"]["description"] = "changed"
    snapshot.append({"name": "extra"})
    assert interp.output_fields == [{"name": "answer", "type": "str", "metadata": {"description": "original"}}]
    assert interp.execute("SUBMIT(answer=str(value + 1))").output == {"answer": "42"}
    interp.shutdown()


def test_public_final_output_label_is_stable() -> None:
    from fleet_rlm.daytona.interpreter import PUBLIC_FINAL_OUTPUT_LABEL

    assert PUBLIC_FINAL_OUTPUT_LABEL == "FINAL submitted"
