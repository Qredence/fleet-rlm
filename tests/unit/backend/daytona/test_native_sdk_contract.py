"""Replay the installed SDK transport; these tests do not certify remote containment."""

import json
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from daytona._async import code_interpreter as sdk


def _interpreter(monkeypatch, chunks):
    messages = [SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(chunk)) for chunk in chunks]
    messages.append(SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None))
    ws = SimpleNamespace(
        receive=AsyncMock(side_effect=messages),
        send_str=AsyncMock(),
        close=AsyncMock(),
        closed=False,
        close_code=1000,
    )
    session = SimpleNamespace(ws_connect=AsyncMock(return_value=ws))
    monkeypatch.setattr(sdk, "http_session_of", lambda _: session)
    api = SimpleNamespace(
        api_client=object(),
        _execute_interpreter_code_serialize=Mock(return_value=(None, "https://example.invalid/execute", {})),
    )
    return sdk.AsyncCodeInterpreter(api), ws


@pytest.mark.asyncio
async def test_exact_sdk_accumulates_output_even_with_a_callback(monkeypatch):
    assert version("daytona") == "0.210.0"
    interpreter, ws = _interpreter(monkeypatch, [{"type": "stdout", "text": "x" * 4096}] * 32)
    seen = []
    result = await interpreter.run_code(
        "pass",
        context=SimpleNamespace(id="fresh-context"),
        on_stdout=lambda msg: seen.append(len(msg.output)),
        timeout=3,
    )
    assert len(result.stdout) == 32 * 4096
    assert seen == [4096] * 32
    assert json.loads(ws.send_str.call_args.args[0])["contextId"] == "fresh-context"
    ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_exact_sdk_does_not_await_output_handler(monkeypatch):
    interpreter, _ = _interpreter(monkeypatch, [{"type": "stdout", "text": "hello"}])

    class Awaitable:
        awaited = False

        def __await__(self):
            self.awaited = True
            yield

    pending = Awaitable()
    await interpreter.run_code("pass", on_stdout=lambda _: pending)
    assert not pending.awaited


@pytest.mark.asyncio
async def test_callback_failure_closes_transport_without_claiming_remote_termination(monkeypatch):
    interpreter, ws = _interpreter(monkeypatch, [{"type": "stdout", "text": "overflow"}])

    def reject(_):
        raise ValueError("output budget exhausted")

    with pytest.raises(Exception, match="output budget exhausted"):
        await interpreter.run_code("pass", on_stdout=reject)
    ws.close.assert_awaited_once()
