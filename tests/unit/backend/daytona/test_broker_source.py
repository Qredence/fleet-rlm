"""Pure source-generation seams for the Daytona host-tool broker."""

from __future__ import annotations

import base64
import json

from fleet_rlm.daytona.broker_source import (
    BROKER_SERVER_CODE,
    FINAL_OUTPUT_MARKER,
    TOOL_WRAPPER_TEMPLATE,
    build_submit_setup_code,
    extract_final_payload,
    final_output_frame,
    remote_submit_setup_code,
)


def test_submit_source_supports_typed_and_generic_signatures() -> None:
    typed = build_submit_setup_code([{"name": "answer", "type": "str"}])
    generic = build_submit_setup_code(None)

    assert "def SUBMIT(answer: str)" in typed
    assert "def SUBMIT(**kwargs)" in generic
    assert "FleetFinalOutputError" in typed
    assert "_FINAL_OUTPUT_MARKER" in generic


def test_remote_submit_setup_is_self_contained() -> None:
    source = remote_submit_setup_code([{"name": "answer", "type": "str"}])

    namespace: dict[str, object] = {}
    exec(source, namespace, namespace)
    try:
        namespace["SUBMIT"](answer="done")  # type: ignore[operator]
    except BaseException as exc:
        assert type(exc).__name__ == "FleetFinalOutputError"
        assert getattr(exc, "value", None) == {"answer": "done"}
    else:
        raise AssertionError("SUBMIT must terminate execution with its final value")


def test_final_output_frames_round_trip_and_accept_legacy_plain_payload() -> None:
    value = {"answer": "done", "count": 2}
    frame = final_output_frame(value)

    assert extract_final_payload(frame) == value
    plain = json.dumps(value, ensure_ascii=False)
    assert extract_final_payload(f"{FINAL_OUTPUT_MARKER}{plain}{FINAL_OUTPUT_MARKER}") == value

    encoded = frame[len(FINAL_OUTPUT_MARKER) : -len(FINAL_OUTPUT_MARKER)]
    assert base64.b64decode(encoded).decode("utf-8") == json.dumps(value, ensure_ascii=False)


def test_broker_server_and_wrapper_sources_are_provider_independent() -> None:
    assert "http.server" in BROKER_SERVER_CODE
    assert "__BROKER_SECRET__" in BROKER_SERVER_CODE
    assert "{broker_port}" in TOOL_WRAPPER_TEMPLATE
    assert "daytona" not in (BROKER_SERVER_CODE + TOOL_WRAPPER_TEMPLATE).lower()
