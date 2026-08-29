"""Pure source-generation seams for the Daytona host-tool broker."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from fleet_rlm.daytona.broker import (
    BROKER_SERVER_CODE,
    FINAL_OUTPUT_MARKER,
    TOOL_WRAPPER_TEMPLATE,
    DaytonaHttpToolBroker,
    build_submit_setup_code,
    extract_final_payload,
    final_output_frame,
    remote_submit_setup_code,
    reset_binding_source,
)


def _build_sandbox_wrapper(tool_name: str, tool_func: Callable[..., object]) -> Callable[..., object]:
    broker = DaytonaHttpToolBroker(sandbox=object())
    namespace: dict[str, object] = {}
    exec(broker._tool_wrapper_source(tool_name, tool_func), namespace, namespace)
    wrapper = namespace[tool_name]
    assert callable(wrapper)
    return wrapper


class _StubbedHTTPResponse:
    """Minimal context-manager response returned by the stubbed ``urlopen``."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _StubbedHTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _write_workspace_text_stub(path: str, content: str, overwrite: bool = False) -> dict[str, object]:
    raise AssertionError(
        f"generated wrapper must never call the host function in-process: {path} {content} {overwrite}"
    )


def test_submit_source_supports_typed_and_generic_signatures() -> None:
    typed = build_submit_setup_code([{"name": "answer", "type": "str"}])
    generic = build_submit_setup_code(None)

    assert "def SUBMIT(answer: str)" in typed
    assert "def SUBMIT(**kwargs)" in generic
    assert "FleetFinalOutputError" in typed
    assert "FINAL_OUTPUT_MARKER" in generic


def test_remote_submit_setup_is_self_contained() -> None:
    source = remote_submit_setup_code([{"name": "answer", "type": "str"}])

    namespace: dict[str, object] = {}
    exec(source, namespace, namespace)
    with pytest.raises(Exception) as raised:
        namespace["SUBMIT"](answer="done")  # type: ignore[operator]
    assert type(raised.value).__name__ == "FleetFinalOutputError"
    assert getattr(raised.value, "value", None) == {"answer": "done"}


def test_binding_reset_source_removes_stale_tool_names() -> None:
    namespace: dict[str, object] = {"old_tool": object(), "SUBMIT": object(), "keep": object()}
    exec(reset_binding_source(("old_tool",)), namespace, namespace)
    assert "old_tool" not in namespace
    assert "SUBMIT" not in namespace
    assert "keep" in namespace


def test_typed_string_submit_rejects_structured_values_and_accepts_json_text() -> None:
    source = remote_submit_setup_code([{"name": "answer", "type": "str"}])

    namespace: dict[str, object] = {}
    exec(source, namespace, namespace)

    with pytest.raises(TypeError, match=r"json\.dumps"):
        namespace["SUBMIT"](answer={"value": 1})  # type: ignore[operator]

    formatted = json.dumps({"value": 1}, ensure_ascii=False, indent=2)
    with pytest.raises(Exception) as raised:
        exec("SUBMIT(answer=json.dumps({'value': 1}, ensure_ascii=False, indent=2))", namespace, namespace)
    assert type(raised.value).__name__ == "FleetFinalOutputError"
    assert getattr(raised.value, "value", None) == {"answer": formatted}


def test_final_output_frames_round_trip_and_accept_legacy_plain_payload() -> None:
    value = {"answer": "done", "count": 2}
    frame = final_output_frame(value)

    assert extract_final_payload(frame) == value
    plain = json.dumps(value, ensure_ascii=False)
    assert extract_final_payload(f"{FINAL_OUTPUT_MARKER}{plain}{FINAL_OUTPUT_MARKER}") == value

    encoded = frame[len(FINAL_OUTPUT_MARKER) : -len(FINAL_OUTPUT_MARKER)]
    assert base64.b64decode(encoded).decode("utf-8") == json.dumps(value, ensure_ascii=False)


def test_startup_command_failure_retains_created_session_for_cleanup() -> None:
    class Filesystem:
        def __init__(self) -> None:
            self.uploaded = b""

        def upload_file(self, content: bytes, _path: str) -> None:
            self.uploaded = content

    class Process:
        def __init__(self, filesystem: Filesystem) -> None:
            self.filesystem = filesystem
            self.created: list[str] = []
            self.deleted: list[str] = []

        def code_run(self, _code: str) -> SimpleNamespace:
            return SimpleNamespace(result=hashlib.sha256(self.filesystem.uploaded).hexdigest())

        def create_session(self, session_id: str) -> None:
            self.created.append(session_id)

        def execute_session_command(self, _session_id: str, _request: object) -> None:
            raise RuntimeError("broker startup command failed")

        def delete_session(self, session_id: str) -> None:
            self.deleted.append(session_id)

    filesystem = Filesystem()
    process = Process(filesystem)
    sandbox = SimpleNamespace(fs=filesystem, process=process)
    broker = DaytonaHttpToolBroker(sandbox=sandbox)

    with pytest.raises(RuntimeError, match="startup command failed"):
        broker.ensure_started()

    assert process.created
    assert broker._broker_session_id == process.created[0]
    broker.stop(strict=True)
    assert process.deleted == process.created


def test_stop_retains_failed_cleanup_ownership_for_retry() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def close(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("client close failed")

    class Process:
        def __init__(self) -> None:
            self.calls = 0

        def delete_session(self, session_id: str) -> None:
            assert session_id == "broker-session"
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("session delete failed")

    client = Client()
    process = Process()
    broker = DaytonaHttpToolBroker(sandbox=SimpleNamespace(process=process))
    broker._client = client  # type: ignore[assignment]
    broker._broker_session_id = "broker-session"
    broker._broker_url = "https://preview"
    broker._broker_token = "token"

    with pytest.raises(RuntimeError, match="client close failed"):
        broker.stop(strict=True)
    assert broker._client is client
    assert broker._broker_session_id == "broker-session"

    broker.stop(strict=True)
    assert client.calls == 2
    assert process.calls == 2
    assert broker._client is None
    assert broker._broker_session_id is None


def test_broker_server_and_wrapper_sources_are_provider_independent() -> None:
    assert "http.server" in BROKER_SERVER_CODE
    assert "__BROKER_SECRET__" in BROKER_SERVER_CODE
    assert "{broker_port}" in TOOL_WRAPPER_TEMPLATE
    assert "daytona" not in (BROKER_SERVER_CODE + TOOL_WRAPPER_TEMPLATE).lower()


def test_tool_wrapper_forwards_every_parameter_as_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """RC-1: required parameters cross the wire by name; ``args`` stays empty."""
    wrapper = _build_sandbox_wrapper("write_workspace_text", _write_workspace_text_stub)
    captured: list[dict[str, object]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _StubbedHTTPResponse:
        del timeout
        captured.append(json.loads(bytes(request.data).decode("utf-8")))
        return _StubbedHTTPResponse({"result": {"ok": True}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # Sandbox-side ergonomics stay positional/kwargs mixed, exactly as model
    # code writes them against the unchanged wrapper signature.
    assert wrapper("notes/todo.md", "hello fleet", overwrite=True) == {"ok": True}
    assert wrapper("notes/todo.md", "hello fleet") == {"ok": True}

    assert len(captured) == 2
    assert captured[0]["tool_name"] == "write_workspace_text"
    assert captured[0]["args"] == []
    assert captured[0]["kwargs"] == {
        "path": "notes/todo.md",
        "content": "hello fleet",
        "overwrite": True,
    }
    assert captured[1]["args"] == []
    assert captured[1]["kwargs"] == {
        "path": "notes/todo.md",
        "content": "hello fleet",
        "overwrite": False,
    }


def test_tool_wrapper_surfaces_http_error_body_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """RC-3: a 500-with-body broker answer surfaces the safe categorized message."""
    wrapper = _build_sandbox_wrapper("write_workspace_text", _write_workspace_text_stub)

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _StubbedHTTPResponse:
        del timeout
        body = json.dumps({"error": "Session Workspace request is invalid"}).encode("utf-8")
        raise urllib.error.HTTPError(request.full_url, 500, "Internal Server Error", None, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Tool call failed: Session Workspace request is invalid"):
        wrapper("notes/todo.md", "hello fleet")


def test_tool_wrapper_falls_back_to_http_status_when_error_body_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _build_sandbox_wrapper("write_workspace_text", _write_workspace_text_stub)

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _StubbedHTTPResponse:
        del timeout
        raise urllib.error.HTTPError(
            request.full_url, 503, "Service Unavailable", None, io.BytesIO(b"upstream unavailable")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match=r"Tool call failed: HTTP 503"):
        wrapper("notes/todo.md", "hello fleet")


def test_project_tool_wrapper_forwards_every_parameter_as_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR-E: project deliverable tools keep the kwargs-only wire contract."""
    from fleet_rlm.workspace.projects import ProjectToolHost

    host = ProjectToolHost(None, max_file_bytes=1024)  # type: ignore[arg-type]
    tool = {str(item.name): item for item in host.as_tools()}["write_project_text"]
    wrapper = _build_sandbox_wrapper("write_project_text", tool.func)
    captured: list[dict[str, object]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _StubbedHTTPResponse:
        del timeout
        captured.append(json.loads(bytes(request.data).decode("utf-8")))
        return _StubbedHTTPResponse({"result": {"ok": True}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert wrapper("fleet-rlm/reports/review.md", "durable review", overwrite=True) == {"ok": True}
    assert wrapper("fleet-rlm/reports/review.md", "durable review") == {"ok": True}

    assert len(captured) == 2
    assert captured[0]["tool_name"] == "write_project_text"
    assert captured[0]["args"] == []
    assert captured[0]["kwargs"] == {
        "path": "fleet-rlm/reports/review.md",
        "content": "durable review",
        "overwrite": True,
    }
    assert captured[1]["args"] == []
    assert captured[1]["kwargs"] == {
        "path": "fleet-rlm/reports/review.md",
        "content": "durable review",
        "overwrite": False,
    }
