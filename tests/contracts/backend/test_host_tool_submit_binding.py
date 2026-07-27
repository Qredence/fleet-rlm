"""Contract: Daytona interpreter exposes host-tool / SUBMIT FinalOutput surface."""

from __future__ import annotations

from fleet_rlm.daytona.http_broker import extract_final_payload, remote_submit_setup_code
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm
from fleet_rlm.rlm.dspy_interpreter_contract import FinalOutput
from fleet_rlm.rlm.signature import FleetRLMSignature


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
    assert "__FLEET_FINAL_OUTPUT__" in source
    payload = extract_final_payload('__FLEET_FINAL_OUTPUT__{"answer": "x"}__FLEET_FINAL_OUTPUT__')
    assert payload == {"answer": "x"}


def test_final_output_type_is_dspy_final_output() -> None:
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()
    result = interp.execute('SUBMIT(answer="ok")')
    assert type(result) is FinalOutput


def test_stock_rlm_resets_tools_registered_on_inject() -> None:
    """Installed DSPy must clear _tools_registered on each inject cycle."""
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interp._tools_registered = True
    rlm = build_native_rlm(
        signature=FleetRLMSignature,
        options=RLMOptions(max_iterations=1),
        interpreter=interp,
    )
    rlm._inject_execution_context(interp, {})
    assert interp._tools_registered is False
