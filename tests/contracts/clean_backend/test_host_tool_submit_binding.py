"""Contract: Daytona interpreter exposes host-tool / SUBMIT FinalOutput surface."""

from __future__ import annotations

from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm_clean.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm_clean.daytona.submit import extract_final_payload, remote_submit_setup_code


def test_interpreter_declares_rlm_injection_surface() -> None:
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    assert hasattr(interp, "tools")
    assert hasattr(interp, "output_fields")
    assert hasattr(interp, "_tools_registered")
    assert callable(interp.start)
    assert callable(interp.execute)
    assert callable(interp.shutdown)


def test_remote_submit_setup_emits_marker_payload() -> None:
    source = remote_submit_setup_code([{"name": "answer", "type": "str"}])
    assert "def SUBMIT(answer: str)" in source
    assert "__FLEET_CLEAN_FINAL_OUTPUT__" in source
    payload = extract_final_payload('__FLEET_CLEAN_FINAL_OUTPUT__{"answer": "x"}__FLEET_CLEAN_FINAL_OUTPUT__')
    assert payload == {"answer": "x"}


def test_final_output_type_is_dspy_final_output() -> None:
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()
    result = interp.execute('SUBMIT(answer="ok")')
    assert type(result) is FinalOutput
