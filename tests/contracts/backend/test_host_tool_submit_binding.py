"""Contract: Daytona interpreter exposes host-tool / SUBMIT FinalOutput surface."""

from __future__ import annotations

import base64
import json

from fleet_rlm.daytona.broker_source import extract_final_payload, remote_submit_setup_code
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm
from fleet_rlm.rlm.dspy_interpreter_contract import FinalOutput
from fleet_rlm.rlm.signature import FleetRLMSignature


def test_interpreter_declares_rlm_injection_surface() -> None:
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    assert hasattr(interp, "tools")
    assert hasattr(interp, "output_fields")
    assert callable(interp.start)
    assert callable(interp.execute)
    assert callable(interp.shutdown)


def test_remote_submit_setup_emits_marker_payload() -> None:
    source = remote_submit_setup_code([{"name": "answer", "type": "str"}])
    assert "def SUBMIT(answer: str)" in source
    assert "__FLEET_FINAL_OUTPUT__" in source
    payload = extract_final_payload('__FLEET_FINAL_OUTPUT__{"answer": "x"}__FLEET_FINAL_OUTPUT__')
    assert payload == {"answer": "x"}


def test_submit_payload_encoding_survives_marker_text_in_json() -> None:
    from fleet_rlm.daytona.broker_source import FINAL_OUTPUT_MARKER

    encoded = base64.b64encode(
        json.dumps({"answer": f"left {FINAL_OUTPUT_MARKER} right"}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")

    assert extract_final_payload(f"{FINAL_OUTPUT_MARKER}{encoded}{FINAL_OUTPUT_MARKER}") == {
        "answer": f"left {FINAL_OUTPUT_MARKER} right"
    }


def test_final_output_type_is_dspy_final_output() -> None:
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()
    result = interp.execute('SUBMIT(answer="ok")')
    assert type(result) is FinalOutput


def test_fleet_generation_marks_bindings_dirty_on_native_inject() -> None:
    """Native DSPy injection advances Fleet-owned generation state."""
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    initial_generation = interp._binding_generation
    rlm = build_native_rlm(
        signature=FleetRLMSignature,
        options=RLMOptions(max_iters=1),
    )
    rlm._inject_execution_context(interp, {})
    assert interp._binding_generation > initial_generation
