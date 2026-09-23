"""Preamble-source contracts for the live Sandbox interpreter.

The Sandbox backend receives Python source, not objects, so Fleet materializes
each RLM input field and the SUBMIT contract into the preamble. That generated
source must be valid Python: JSON literals such as ``true``/``false``/``null``
are valid identifiers and therefore runtime ``NameError``s, not syntax errors,
which silently poisons every action in the Turn because the preamble is
replayed per execution.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import extract_final_payload, final_output_frame, sandbox_backend
from fleet_rlm.rlm.program import WorkspaceCapabilityMetadata, build_session_context_payload
from fleet_rlm.sessions.context import SessionContextManifest, TurnPreview

_OUTPUT_FIELDS = [{"name": "answer", "type": "str", "required": True}]


def _sandbox_backend(*, stdout: str = "", stderr: str = "", error: str | None = None) -> Any:
    """A Sandbox backend whose remote execution returns the given result."""
    code_interpreter = MagicMock()
    code_interpreter.create_context.return_value = "ctx"
    code_interpreter.run_code.return_value = MagicMock(stdout=stdout, stderr=stderr, error=error)
    sandbox = MagicMock()
    sandbox.code_interpreter = code_interpreter

    backend = sandbox_backend(sandbox)
    backend.ensure_submit(_OUTPUT_FIELDS)
    return backend


def _emitted_sandbox_source(*, code: str = "pass", variables: dict[str, Any] | None = None) -> str:
    """Return the exact source the Sandbox backend would execute for one action."""
    backend = _sandbox_backend()
    backend.run(code, variables=variables)

    return backend.sandbox.code_interpreter.run_code.call_args[0][0]


def _exec_source(source: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec(compile(source, "<sandbox>", "exec"), namespace, namespace)
    return namespace


def _production_session_context() -> dict[str, Any]:
    """The exact Session context payload shape that broke trace tr-07d7a175."""
    return build_session_context_payload(
        session_context=SessionContextManifest(
            session_id=UUID("d3b155d7-028c-435b-9e40-43e758f29d12"),
            checkpoint_version=3,
            message_count=2,
            recent=(
                TurnPreview(ordinal=1, role="user", preview="ADD A 5"),
                TurnPreview(ordinal=2, role="assistant", preview="working"),
            ),
        ),
        workspace=WorkspaceCapabilityMetadata(available=True, root=".", instructions="use /workspace"),
    )


def test_json_booleans_and_null_are_bound_without_name_error() -> None:
    """``true``/``false``/``null`` must be decoded, not executed as Python names."""
    variables = {"flag_on": True, "flag_off": False, "empty": None}

    namespace = _exec_source(_emitted_sandbox_source(variables=variables))

    assert namespace["flag_on"] is True
    assert namespace["flag_off"] is False
    assert namespace["empty"] is None


def test_production_session_context_payload_round_trips_exactly() -> None:
    """Regression: tr-07d7a175 died on ``session_context.workspace.available == true``."""
    session_context = _production_session_context()
    assert session_context["workspace"]["available"] is True

    source = _emitted_sandbox_source(variables={"request": "print(request)", "session_context": session_context})
    namespace = _exec_source(source)

    assert namespace["session_context"] == session_context
    assert namespace["request"] == "print(request)"
    assert "_fleet_bindings_json.loads(" in source


def test_bound_inputs_are_readable_by_model_code_in_same_execution() -> None:
    """The preamble must not abort before the model's own action runs."""
    session_context = _production_session_context()

    namespace = _exec_source(
        _emitted_sandbox_source(
            code="observed = sorted(session_context['workspace'].keys())",
            variables={"request": "hi", "session_context": session_context},
        )
    )

    assert namespace["observed"] == ["available", "instructions", "root"]


def test_nested_containers_and_non_ascii_round_trip() -> None:
    """Nested values keep identity, container types, and non-ASCII content."""
    variables = {
        "skill_cards": [{"id": "s1", "resources_available": True, "affordances": ["run", "read"]}],
        "attachments": [{"id": "a1", "byte_size": 12, "content_type": None}],
        "note": "caf\u00e9 na\u00efve" + chr(0x2028) + " line separator",
        "counts": {"ints": [1, 2], "floats": [1.5, -0.25]},
    }

    namespace = _exec_source(_emitted_sandbox_source(variables=variables))

    for key, value in variables.items():
        assert namespace[key] == value, key


def test_non_finite_floats_are_bound_without_name_error() -> None:
    """``NaN``/``Infinity`` are likewise not Python names in generated source."""
    namespace = _exec_source(_emitted_sandbox_source(variables={"score": float("nan"), "cap": float("inf")}))

    assert namespace["score"] != namespace["score"]  # NaN is never equal to itself
    assert namespace["cap"] == float("inf")


def test_empty_variable_mapping_emits_no_binding_import() -> None:
    """An action without bound inputs must not carry a dangling import or stubs."""
    source = _emitted_sandbox_source(code="x = 1", variables={})

    assert "_fleet_bindings_json" not in source
    assert _exec_source(source)["x"] == 1


def test_sandbox_preamble_defines_the_submit_exception_it_raises() -> None:
    """Regression: live ``SUBMIT`` printed its frame, then died on an undefined name."""
    namespace = _exec_source(_emitted_sandbox_source(variables={"request": "hi"}))

    assert "FleetFinalOutputError" in namespace


def test_submit_aborts_with_the_preamble_defined_exception() -> None:
    """``SUBMIT`` must stop execution with a real exception carrying the payload."""
    namespace = _exec_source(_emitted_sandbox_source(variables={"request": "hi"}))

    with pytest.raises(namespace["FleetFinalOutputError"]) as exc_info:
        namespace["SUBMIT"](answer="hello")

    assert exc_info.value.value == {"answer": "hello"}


def test_submit_frame_survives_the_abort(capsys: pytest.CaptureFixture[str]) -> None:
    """The stdout frame is the transport, so it must be emitted before the abort."""
    namespace = _exec_source(_emitted_sandbox_source(variables={"request": "hi"}))

    with pytest.raises(namespace["FleetFinalOutputError"]):
        namespace["SUBMIT"](answer="hello")

    assert extract_final_payload(capsys.readouterr().out) == {"answer": "hello"}


def test_declared_variables_all_reach_the_sandbox() -> None:
    """Telemetry counts declared variables, so none may be dropped in silence."""
    variables: dict[str, Any] = {
        "a": 1,
        "b": "two",
        "c": True,
        "d": None,
        "e": [1, 2],
        "f": {"g": 1},
        "h": (1, 2),
    }

    namespace = _exec_source(_emitted_sandbox_source(variables=variables))

    assert {name for name in variables if name in namespace} == set(variables)
    assert namespace["h"] == [1, 2]


def test_unrepresentable_binding_fails_closed_naming_the_variable() -> None:
    """An input the transport cannot carry is a host contract error, not a NameError."""
    with pytest.raises(DaytonaAdapterError, match=r"binding 'opaque' of type object"):
        _emitted_sandbox_source(variables={"request": "hi", "opaque": object()})


def test_nested_unrepresentable_binding_fails_closed() -> None:
    """Representability is decided by JSON itself, including nested values."""
    with pytest.raises(DaytonaAdapterError, match=r"binding 'session_context' of type dict"):
        _emitted_sandbox_source(
            variables={"session_context": {"workspace": {"handle": object()}}},
        )


def test_tool_enabled_action_never_uses_the_direct_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host tools are brokered while model-authored source remains remote."""
    calls: list[dict[str, Any]] = []

    class _Broker:
        def __init__(self, _sandbox: Any, *, port: int, **_kwargs: Any) -> None:
            assert port > 0

        def bind_tools(self, tools: dict[str, Any]) -> None:
            assert set(tools) == {"host_tool"}

        def setup_source(self, source: str) -> str:
            return source

        def execute(self, code: str, variables: dict[str, Any], *, timeout_s: int) -> dict[str, Any]:
            calls.append({"code": code, "variables": variables, "timeout_s": timeout_s})
            return {"stdout": "", "stderr": "", "final": {"answer": "remote"}}

        def stop(self, *, strict: bool) -> None:
            assert strict

    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _Broker)
    backend = _sandbox_backend()
    backend.bind_host_tools({"host_tool": lambda: "host"})

    result = backend.run("host_tool()\nSUBMIT(answer='remote')", {"flag": True})

    assert result.final == {"answer": "remote"}
    assert len(calls) == 1
    assert "host_tool()" in calls[0]["code"]
    assert backend.sandbox.code_interpreter.run_code.call_count == 0


def test_submit_abort_is_not_reported_as_an_execution_error() -> None:
    """The frame is the transport, so the trailing abort must not fail the step."""
    backend = _sandbox_backend(
        stdout=final_output_frame({"answer": "hello"}),
        stderr="FleetFinalOutputError: Final output submitted",
        error="FleetFinalOutputError: Final output submitted",
    )

    result = backend.run("SUBMIT(answer='hello')")

    assert result.final == {"answer": "hello"}
    assert result.error is None


@pytest.mark.asyncio
async def test_broker_resolves_awaitable_tools_and_returns_structured_failure() -> None:
    """Broker polling must not turn host-tool failures into opaque HTTP 500s."""
    import asyncio

    from fleet_rlm.daytona.interpreter import _SyncBridgeLoop

    class _Response:
        def __init__(self, body: dict[str, object]) -> None:
            self._body = body
            self.status_code = 200

        def json(self) -> dict[str, object]:
            return self._body

    class _Client:
        def __init__(self) -> None:
            self.posts: list[dict[str, object]] = []
            self._requests = [
                {"id": "ok-1", "lease": "lease-1", "tool_name": "async_tool", "args": [], "kwargs": {}},
                {"id": "bad-1", "lease": "lease-2", "tool_name": "missing", "args": [], "kwargs": {}},
            ]

        def get(self, _path: str) -> _Response:
            return _Response({"requests": self._requests})

        def post(self, _path: str, *, content: bytes, headers: dict[str, str]) -> _Response:
            assert headers == {"Content-Type": "application/json"}
            self.posts.append(json.loads(content))
            return _Response({"status": "ok"})

    async def async_tool() -> dict[str, object]:
        return {"ok": True}

    broker = DaytonaHttpToolBroker(MagicMock(), port=8765)
    client = _Client()
    broker._client = client  # type: ignore[assignment]
    broker.bind_tools({"async_tool": async_tool})
    broker.bind_async_bridge(_SyncBridgeLoop(caller_loop=asyncio.get_running_loop()))

    await asyncio.to_thread(broker._poll_once)

    assert client.posts[0]["result"] == {"ok": True}
    failure = client.posts[1]["tool_error"]
    assert isinstance(failure, dict)
    assert failure["category"] == "KeyError"
    assert failure["call_id"] == "bad-1"


def test_native_stdout_is_forwarded_before_execution_returns() -> None:
    from types import SimpleNamespace

    backend = _sandbox_backend(stdout="first\nsecond\n")
    chunks: list[str] = []

    def run_code(_code: str, **kwargs: Any) -> Any:
        kwargs["on_stdout"](SimpleNamespace(output="first\n"))
        assert chunks == ["first\n"]
        kwargs["on_stdout"](SimpleNamespace(output="second\n"))
        return SimpleNamespace(stdout="first\nsecond\n", stderr="", error=None)

    backend.sandbox.code_interpreter.run_code.side_effect = run_code
    backend.run("print('first'); print('second')", on_stdout=chunks.append)
    assert chunks == ["first\n", "second\n"]


@pytest.mark.parametrize("method", ["exec", "code_run"])
def test_empty_nonzero_process_result_is_an_error(method: str) -> None:
    from types import SimpleNamespace

    process = SimpleNamespace(**{method: lambda *_args, **_kwargs: SimpleNamespace(result="", exit_code=1)})
    backend = sandbox_backend(SimpleNamespace(process=process))
    assert backend.run("raise SystemExit(1)").error


def test_process_exec_quotes_python_for_the_shell() -> None:
    import shlex
    from types import SimpleNamespace

    commands: list[str] = []

    def execute(command: str, **_kwargs: Any) -> Any:
        commands.append(command)
        return SimpleNamespace(result="", exit_code=0)

    backend = sandbox_backend(SimpleNamespace(process=SimpleNamespace(exec=execute)))
    source = 'value = "$(echo should_not_run)"\nprint(value)'
    backend.run(source)
    arguments = shlex.split(commands[0])
    assert arguments[:2] == ["python3", "-c"]
    assert arguments[2].endswith(source)
