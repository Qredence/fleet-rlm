"""B1: host-tool / SUBMIT broker binding completeness (offline seams)."""

from __future__ import annotations

from typing import Any

import pytest
from dspy.primitives.code_interpreter import FinalOutput


class _RecordingTool:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, name: str) -> str:
        self.calls.append(((name,), {}))
        return f"loaded:{name}"


def test_submit_returns_final_output() -> None:
    from fleet_rlm_clean.daytona.in_process import InProcessInterpreterBackend
    from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    result = interp.execute('SUBMIT(answer="done")')

    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "done"}


def test_sandbox_code_invokes_host_mediated_tool() -> None:
    from fleet_rlm_clean.daytona.in_process import InProcessInterpreterBackend
    from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter

    tool = _RecordingTool()
    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend, tools={"load_skill": tool})
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    result = interp.execute("SUBMIT(answer=load_skill(name='demo'))")

    assert tool.calls == [(("demo",), {})]
    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "loaded:demo"}


def test_llm_query_host_callable_is_reachable_from_sandbox_code() -> None:
    from fleet_rlm_clean.daytona.in_process import InProcessInterpreterBackend
    from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter

    seen: list[str] = []

    def llm_query(prompt: str) -> str:
        seen.append(prompt)
        return f"sub:{prompt}"

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.tools.update({"llm_query": llm_query})
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    result = interp.execute("SUBMIT(answer=llm_query('ping'))")

    assert seen == ["ping"]
    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "sub:ping"}


def test_shutdown_stops_broker_and_rejects_further_execute() -> None:
    from fleet_rlm_clean.daytona.errors import DaytonaAdapterError
    from fleet_rlm_clean.daytona.in_process import InProcessInterpreterBackend
    from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()
    assert isinstance(interp.execute('SUBMIT(answer="ok")'), FinalOutput)
    interp.shutdown()

    with pytest.raises(DaytonaAdapterError):
        interp.execute('SUBMIT(answer="again")')
    assert backend.closed is True


def test_host_tool_public_errors_are_sanitized() -> None:
    from fleet_rlm_clean.daytona.in_process import InProcessInterpreterBackend
    from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter

    def leaky_tool() -> str:
        raise RuntimeError("failed api_key=sk-secret path=/tmp/secret")

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend, tools={"leaky": leaky_tool})
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    with pytest.raises(Exception) as exc_info:
        interp.execute("leaky()")

    message = str(exc_info.value)
    assert "sk-secret" not in message
    assert "/tmp/secret" not in message


def test_http_broker_wrapper_source_and_fulfill_sanitize() -> None:
    from fleet_rlm_clean.daytona.http_broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_url = "http://example.test"
    broker._broker_secret = "secret"
    broker._stopped = False

    source = broker._tool_wrapper_source("load_skill", lambda name: name)
    assert "def load_skill(name)" in source
    assert "X-Broker-Secret" in source

    posted: list[dict[str, object]] = []

    class _Resp:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _fake_urlopen(req: object, timeout: float = 0) -> _Resp:
        import json
        from urllib.request import Request

        assert isinstance(req, Request)
        posted.append(json.loads(req.data.decode("utf-8")))
        return _Resp()

    import urllib.request

    original = urllib.request.urlopen
    urllib.request.urlopen = _fake_urlopen  # type: ignore[method-assign]
    try:
        broker._fulfill(
            {
                "id": "c1",
                "lease_token": "tok",
                "tool_name": "load_skill",
                "args": [],
                "kwargs": {},
            },
            tool_executor=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("api_key=sk-secret path=/tmp/x")),
        )
    finally:
        urllib.request.urlopen = original  # type: ignore[method-assign]

    assert posted
    assert "sk-secret" not in str(posted[0].get("error", ""))
    assert "/tmp/x" not in str(posted[0].get("error", ""))
