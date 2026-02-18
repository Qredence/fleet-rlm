from __future__ import annotations

import io
import json

import pytest

import fleet_rlm.bridge.server as bridge_server
from fleet_rlm.bridge.protocol import BridgeRPCError, parse_request_line
from fleet_rlm.bridge.server import BridgeRuntime, run_stdio_server
from fleet_rlm.config import AppConfig


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_parse_request_line_valid_payload() -> None:
    request = parse_request_line(
        '{"id":"1","method":"status.get","params":{"a":1,"b":"two"}}'
    )
    assert request.request_id == "1"
    assert request.method == "status.get"
    assert request.params == {"a": 1, "b": "two"}


def test_parse_request_line_rejects_invalid_json() -> None:
    with pytest.raises(BridgeRPCError) as exc:
        parse_request_line("{not-json}")
    assert exc.value.code == "INVALID_REQUEST"


def test_run_stdio_server_handles_init_and_shutdown() -> None:
    input_stream = io.StringIO(
        '{"id":"1","method":"session.init","params":{}}\n'
        '{"id":"2","method":"session.shutdown","params":{}}\n'
    )
    output_stream = io.StringIO()

    run_stdio_server(
        config=AppConfig(),
        input_stream=input_stream,
        output_stream=output_stream,
        trace_mode="compact",
    )

    messages = _json_lines(output_stream.getvalue())
    assert messages[0]["id"] == "1"
    assert isinstance(messages[0]["result"]["session_id"], str)
    assert messages[0]["result"]["trace_mode"] == "compact"
    assert messages[0]["result"]["secret_name"] == "LITELLM"
    assert messages[0]["result"]["volume_name"] == "rlm-volume-dspy"
    assert messages[1] == {"id": "2", "result": {"ok": True}}


def test_run_stdio_server_uses_config_secret_and_volume_when_cli_unset() -> None:
    input_stream = io.StringIO(
        '{"id":"1","method":"session.init","params":{}}\n'
        '{"id":"2","method":"session.shutdown","params":{}}\n'
    )
    output_stream = io.StringIO()
    config = AppConfig(
        interpreter={
            "secrets": ["CFG_SECRET"],
            "volume_name": "cfg-volume",
        }
    )

    run_stdio_server(
        config=config,
        input_stream=input_stream,
        output_stream=output_stream,
        trace_mode="compact",
    )

    messages = _json_lines(output_stream.getvalue())
    assert messages[0]["result"]["secret_name"] == "CFG_SECRET"
    assert messages[0]["result"]["volume_name"] == "cfg-volume"


def test_run_stdio_server_cli_secret_and_volume_override_config() -> None:
    input_stream = io.StringIO(
        '{"id":"1","method":"session.init","params":{}}\n'
        '{"id":"2","method":"session.shutdown","params":{}}\n'
    )
    output_stream = io.StringIO()
    config = AppConfig(
        interpreter={
            "secrets": ["CFG_SECRET"],
            "volume_name": "cfg-volume",
        }
    )

    run_stdio_server(
        config=config,
        input_stream=input_stream,
        output_stream=output_stream,
        trace_mode="compact",
        secret_name="CLI_SECRET",
        volume_name="cli-volume",
    )

    messages = _json_lines(output_stream.getvalue())
    assert messages[0]["result"]["secret_name"] == "CLI_SECRET"
    assert messages[0]["result"]["volume_name"] == "cli-volume"


def test_run_stdio_server_returns_error_for_unknown_method() -> None:
    input_stream = io.StringIO('{"id":"9","method":"missing.method","params":{}}\n')
    output_stream = io.StringIO()

    run_stdio_server(
        config=AppConfig(),
        input_stream=input_stream,
        output_stream=output_stream,
        trace_mode="compact",
    )

    messages = _json_lines(output_stream.getvalue())
    assert messages[0]["id"] == "9"
    assert messages[0]["error"]["code"] == "UNKNOWN_METHOD"


def test_run_stdio_server_routes_mentions_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_search_mentions(params: dict[str, object]) -> dict[str, object]:
        captured.update(params)
        return {
            "items": [],
            "query": str(params.get("query", "")),
            "root": "/tmp",
            "count": 0,
        }

    monkeypatch.setattr(bridge_server, "search_mentions", fake_search_mentions)
    input_stream = io.StringIO(
        '{"id":"1","method":"mentions.search","params":{"query":"src/"}}\n'
        '{"id":"2","method":"session.shutdown","params":{}}\n'
    )
    output_stream = io.StringIO()

    run_stdio_server(
        config=AppConfig(),
        input_stream=input_stream,
        output_stream=output_stream,
        trace_mode="compact",
    )

    messages = _json_lines(output_stream.getvalue())
    assert messages[0]["id"] == "1"
    assert messages[0]["result"]["query"] == "src/"
    assert captured["query"] == "src/"


def test_runtime_dispatch_memory_read_uses_runtime_volume_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_params: dict[str, object] = {}

    def fake_memory_read(params: dict[str, object]) -> dict[str, object]:
        captured_params.update(params)
        return {"ok": True, "volume_name": params.get("volume_name")}

    monkeypatch.setattr(bridge_server, "memory_read", fake_memory_read)
    runtime = BridgeRuntime(
        config=AppConfig(),
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        volume_name="runtime-volume",
    )
    request = parse_request_line(
        '{"id":"1","method":"memory.read","params":{"key":"session-state"}}'
    )

    result = runtime.dispatch(request)

    assert captured_params["volume_name"] == "runtime-volume"
    assert result["volume_name"] == "runtime-volume"


def test_runtime_dispatch_memory_read_preserves_explicit_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_params: dict[str, object] = {}

    def fake_memory_read(params: dict[str, object]) -> dict[str, object]:
        captured_params.update(params)
        return {"ok": True, "volume_name": params.get("volume_name")}

    monkeypatch.setattr(bridge_server, "memory_read", fake_memory_read)
    runtime = BridgeRuntime(
        config=AppConfig(),
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        volume_name="runtime-volume",
    )
    request = parse_request_line(
        '{"id":"1","method":"memory.read","params":{"key":"session-state","volume_name":"explicit-volume"}}'
    )

    result = runtime.dispatch(request)

    assert captured_params["volume_name"] == "explicit-volume"
    assert result["volume_name"] == "explicit-volume"


def test_emit_event_flushes_token_batch_before_trajectory_step() -> None:
    input_stream = io.StringIO()
    output_stream = io.StringIO()
    runtime = BridgeRuntime(
        config=AppConfig(),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    runtime.emit_event(
        method="chat.event",
        params={"kind": "assistant_token", "text": "Hello "},
    )
    runtime.emit_event(
        method="chat.event",
        params={
            "kind": "trajectory_step",
            "text": "step #0",
            "flush_tokens": True,
        },
    )

    messages = _json_lines(output_stream.getvalue())
    assert len(messages) == 2

    first = messages[0]
    second = messages[1]

    assert first["event"] == "chat.event"
    assert first["params"]["kind"] == "assistant_token_batch"
    assert first["params"]["text"] == "Hello "

    assert second["event"] == "chat.event"
    assert second["params"]["kind"] == "trajectory_step"
    assert second["params"]["text"] == "step #0"

    assert int(first["seq"]) < int(second["seq"])
