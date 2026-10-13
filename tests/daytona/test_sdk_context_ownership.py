"""SDK context and synchronous client ownership at the worker boundary."""

import asyncio
import contextlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from daytona._sync.code_interpreter import CodeInterpreter
from daytona.common.errors import DaytonaError
from wsproto.events import CloseConnection, TextMessage

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import (
    _MAX_CAPTURED_OUTPUT_CHARS,
    _OUTPUT_TRUNCATION_NOTICE,
    FINAL_OUTPUT_MARKER,
    DaytonaCodeInterpreter,
    SyncBridgeDispatcher,
    sandbox_backend,
    sync_sandbox,
)
from fleet_rlm.paths import VolumePaths
from fleet_rlm.rlm.event_types import RLMOutput
from fleet_rlm.workspace.host_io import DaytonaRunStorage
from tests.support.sdk_interpreter import LocalSDKInterpreter


@pytest.fixture
def broker(monkeypatch):
    instance = MagicMock()
    instance.setup_source.side_effect = lambda source: source
    instance.service_execution.side_effect = lambda run, **_kwargs: run()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", lambda *_args, **_kwargs: instance)
    return instance


def test_official_sync_client_closes_after_context_and_broker(broker):
    service = LocalSDKInterpreter()
    client = MagicMock()
    client.get.return_value = SimpleNamespace(code_interpreter=service)
    dispatcher = SyncBridgeDispatcher()
    dispatcher.sync_client_factory = lambda: client
    # Supplying a loop routes application bridges; production workers select
    # the official synchronous client instead, without scheduling SDK coroutines.
    loop = asyncio.new_event_loop()
    try:
        backend = sandbox_backend(SimpleNamespace(id="root"), loop=loop, dispatcher=dispatcher)
        assert backend.run("cached = 41").error is None
        assert backend.run("print(cached + 1)").stdout == "42\n"
        client.get.assert_called_once_with("root")
        assert len(service.contexts) == 1
        backend.close()
        assert len(service.deleted) == 1
        broker.stop.assert_called_once_with(strict=True)
        client._subscription_manager.shutdown.assert_called_once()
        client._event_dispatcher.disconnect.assert_called_once()
        client._http_client.close.assert_called_once()
        client._api_client.rest_client.pool_manager.clear.assert_called_once()
        client._toolbox_api_client.rest_client.pool_manager.clear.assert_called_once()
    finally:
        loop.close()


def test_failed_context_delete_remains_owned_and_retries_without_execution(broker):
    service = MagicMock()
    service.run_code.return_value = SimpleNamespace(stdout="", stderr="", error=None)
    service.delete_context.side_effect = [RuntimeError("delete failed"), None]
    backend = sandbox_backend(SimpleNamespace(code_interpreter=service))
    backend.run("value = 1")
    context = backend._sdk_context
    with pytest.raises(RuntimeError, match="delete failed"):
        backend.close()
    assert backend._sdk_context is context
    broker.stop.assert_called_once_with(strict=True)
    backend.close()
    assert backend._sdk_context is None
    assert service.delete_context.call_count == 2
    assert service.run_code.call_count == 2  # setup plus one action


def test_stream_failure_never_repeats_execution_or_side_effect(broker):
    service = MagicMock()
    effects = []

    def run_code(code, **_kwargs):
        if "def SUBMIT" in code:
            return SimpleNamespace(stdout="", stderr="", error=None)
        effects.append("tool mutation")
        raise TimeoutError("SDK stream disconnected after mutation")

    service.run_code.side_effect = run_code
    backend = sandbox_backend(SimpleNamespace(code_interpreter=service))
    with pytest.raises(Exception, match="SDK stream disconnected"):
        backend.run("tool_mutation(); print('done')")
    assert effects == ["tool mutation"]
    assert broker.service_execution.call_count == 2
    assert service.run_code.call_count == 2
    backend.close()


def test_shutdown_uses_the_pinned_generated_clients_real_resource_surface():
    from daytona_api_client import ApiClient
    from daytona_toolbox_api_client import ApiClient as ToolboxApiClient

    from fleet_rlm.daytona.provider import close_sync_daytona_client

    api, toolbox = ApiClient(), ToolboxApiClient()
    api_clear = MagicMock(wraps=api.rest_client.pool_manager.clear)
    toolbox_clear = MagicMock(wraps=toolbox.rest_client.pool_manager.clear)
    api.rest_client.pool_manager.clear = api_clear
    toolbox.rest_client.pool_manager.clear = toolbox_clear
    client = SimpleNamespace(
        _subscription_manager=MagicMock(),
        _event_dispatcher=MagicMock(),
        _http_client=MagicMock(),
        _api_client=api,
        _toolbox_api_client=toolbox,
        _tracer_provider=None,
    )
    close_sync_daytona_client(client)
    api_clear.assert_called_once()
    toolbox_clear.assert_called_once()
    client._http_client.close.assert_called_once()


def test_shutdown_attempts_all_resources_when_one_close_fails():
    from fleet_rlm.daytona.provider import close_sync_daytona_client

    client = MagicMock()
    client._http_client.close.side_effect = RuntimeError("close failed")
    with pytest.raises(RuntimeError, match="close failed"):
        close_sync_daytona_client(client)
    client._api_client.rest_client.pool_manager.clear.assert_called_once()
    client._toolbox_api_client.rest_client.pool_manager.clear.assert_called_once()
    client._event_dispatcher.disconnect.assert_called_once()
    client._tracer_provider.shutdown.assert_called_once()


@pytest.fixture
def sdk_wire(monkeypatch, broker):
    """Exercise pinned SDK accumulation with an offline execution/WebSocket peer."""
    peer = LocalSDKInterpreter()
    contexts = {}
    wire = SimpleNamespace(fragment_chars=4096, actions=[], results=[], contexts=contexts, peer=peer, broker=broker)
    api = MagicMock()
    api._execute_interpreter_code_serialize.return_value = ("POST", "http://sdk.invalid/code", {})

    def create_context(**_kwargs):
        context_id = str(len(contexts) + len(peer.deleted))
        contexts[context_id] = peer.create_context()
        return SimpleNamespace(id=context_id)

    def delete_context(**kwargs):
        peer.delete_context(contexts.pop(kwargs["id"]))

    api.create_interpreter_context.side_effect = create_context
    api.delete_interpreter_context.side_effect = delete_context
    wire.delete_context = delete_context

    class Socket:
        def send_text(self, raw):
            request = json.loads(raw)
            messages = []

            def output(stream, message):
                fragment = wire.fragment_chars
                for offset in range(0, len(message.output), fragment):
                    messages.append({"type": stream, "text": message.output[offset : offset + fragment]})

            result = peer.run_code(
                request["code"],
                context=contexts[request["contextId"]],
                on_stdout=lambda message: output("stdout", message),
                on_stderr=lambda message: output("stderr", message),
            )
            if result.error is not None:
                messages.append(
                    {"type": "error", "name": result.error.name, "value": result.error.value, "traceback": ""}
                )
            wire.actions.append(messages)
            self.messages = iter(messages)

        def receive(self):
            message = next(self.messages, None)
            return CloseConnection(code=1000) if message is None else TextMessage(data=json.dumps(message))

    @contextlib.contextmanager
    def connect(*_args, **_kwargs):
        yield Socket()

    monkeypatch.setattr("httpx_ws.connect_ws", connect)

    class RecordingSDK(CodeInterpreter):
        def run_code(self, *args, **kwargs):
            result = super().run_code(*args, **kwargs)
            wire.results.append(result)
            return result

    wire.service = RecordingSDK(api, object())
    wire.api = api
    return wire


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_pinned_sdk_accumulates_only_bounded_output_and_execution_continues(sdk_wire, stream):
    backend = sandbox_backend(SimpleNamespace(code_interpreter=sdk_wire.service))
    code = f"import sys\nsys.{stream}.write('x' * (2 * 1024 * 1024))\nfinished = True"
    result = backend.run(code)
    expected = "x" * _MAX_CAPTURED_OUTPUT_CHARS + _OUTPUT_TRUNCATION_NOTICE
    assert getattr(result, stream) == expected
    assert getattr(sdk_wire.results[-1], stream) == expected
    assert result.error is None
    assert backend.run("print(finished)").stdout == "True\n"
    # The capture budget resets for each action in the same context.
    assert backend.run(code).stdout == (expected if stream == "stdout" else "")
    assert len(sdk_wire.contexts) == 1
    backend.close()


@pytest.mark.parametrize("extra", [0, 1])
def test_sdk_capture_exact_boundary_has_one_notice_only_on_overflow(sdk_wire, extra):
    backend = sandbox_backend(SimpleNamespace(code_interpreter=sdk_wire.service))
    result = backend.run(f"print('x' * ({_MAX_CAPTURED_OUTPUT_CHARS} + {extra}), end='')")
    assert result.stdout == "x" * _MAX_CAPTURED_OUTPUT_CHARS + (_OUTPUT_TRUNCATION_NOTICE if extra else "")
    assert result.error is None
    backend.close()


def test_final_submission_survives_exhausted_streams_with_unicode_and_fragmented_frames(sdk_wire):
    sdk_wire.fragment_chars = 127
    backend = sandbox_backend(SimpleNamespace(code_interpreter=sdk_wire.service))
    backend.ensure_submit([{"name": "answer", "type": "str"}], max_output_chars=1000)
    answer = f"Verified café 😀 {FINAL_OUTPUT_MARKER}"
    chunks = []
    result = backend.run(
        f"import sys\nprint('x' * (2 * 1024 * 1024))\nsys.stderr.write('y' * (2 * 1024 * 1024))\n"
        f"SUBMIT(answer={answer!r})",
        on_stdout=chunks.append,
    )
    assert result.final == {"answer": answer}
    assert result.error is None
    assert result.stdout == "x" * _MAX_CAPTURED_OUTPUT_CHARS + _OUTPUT_TRUNCATION_NOTICE
    assert result.stderr == "y" * _MAX_CAPTURED_OUTPUT_CHARS + _OUTPUT_TRUNCATION_NOTICE
    streamed = "".join(chunks)
    assert streamed.count(FINAL_OUTPUT_MARKER) == 2  # Exactly one private frame.
    assert sdk_wire.results[-1].error.name == "FleetFinalOutputError"
    assert len(sdk_wire.results[-1].stdout) < 70_000
    backend.close()


def test_public_output_never_exposes_the_private_final_frame_after_truncation(sdk_wire):
    details = []
    interpreter = DaytonaCodeInterpreter(
        backend=sandbox_backend(SimpleNamespace(code_interpreter=sdk_wire.service)),
        observer=details.append,
        output_fields=[{"name": "answer", "type": "str"}],
    )
    final = interpreter.execute("print('x' * (2 * 1024 * 1024))\nSUBMIT(answer='private final text')")
    assert final.output == {"answer": "private final text"}
    visible = "".join(detail.output for detail in details if isinstance(detail, RLMOutput))
    assert FINAL_OUTPUT_MARKER not in visible
    assert "private final text" not in visible
    interpreter.shutdown()


def test_sdk_streams_are_restored_after_error_and_next_action_has_fresh_limits(sdk_wire):
    backend = sandbox_backend(SimpleNamespace(code_interpreter=sdk_wire.service))
    result = backend.run("print('x' * (2 * 1024 * 1024))\nraise ValueError('repairable')")
    assert result.error_category == "ValueError"
    assert "repairable" in result.error
    assert backend.run("print('fresh')").stdout == "fresh\n"
    assert backend.run("import sys\nsys.stderr.write('fresh error')").stderr == "fresh error"
    backend.close()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_transport_backstop_is_terminal_fences_replay_and_retains_failed_cleanup(sdk_wire, stream):
    tainted = MagicMock()
    backend = sandbox_backend(SimpleNamespace(code_interpreter=sdk_wire.service), mark_tainted=tainted)
    backend.run("effects = []")
    with pytest.raises(DaytonaAdapterError) as refused:
        backend.run(f"import sys\neffects.append('once')\nsys.{stream}.stream.write('x' * (2 * 1024 * 1024))")
    assert refused.value.cause_type == "InterpreterOutputTransportError"
    action_count = len(sdk_wire.actions)
    with pytest.raises(DaytonaAdapterError):
        backend.run("effects.append('replay')")
    assert len(sdk_wire.actions) == action_count
    assert next(iter(sdk_wire.contexts.values()))["effects"] == ["once"]
    tainted.assert_called_once()
    sdk_wire.api.delete_interpreter_context.side_effect = RuntimeError("cleanup unresolved")
    with pytest.raises(DaytonaError, match="cleanup unresolved"):
        backend.close()
    assert len(sdk_wire.contexts) == 1
    sdk_wire.api.delete_interpreter_context.side_effect = sdk_wire.delete_context
    backend.close()
    assert not sdk_wire.contexts


@pytest.mark.usefixtures("broker")
def test_backstop_failure_serialized_by_an_sdk_double_cannot_become_a_success():
    service = LocalSDKInterpreter()
    tainted = MagicMock()
    backend = sandbox_backend(SimpleNamespace(code_interpreter=service), mark_tainted=tainted)
    with pytest.raises(DaytonaAdapterError) as error:
        backend.run("import sys\nsys.stdout.stream.write('x' * (2 * 1024 * 1024))")
    assert error.value.cause_type == "InterpreterOutputTransportError"
    tainted.assert_called_once()
    backend.close()


@pytest.mark.asyncio
async def test_application_storage_uses_async_bridge_without_creating_a_worker_client():
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    dispatcher.sync_client_factory = MagicMock(side_effect=AssertionError("worker client used by application"))
    sandbox = SimpleNamespace(
        id="root",
        fs=SimpleNamespace(upload_file=AsyncMock()),
        process=SimpleNamespace(),
        code_interpreter=SimpleNamespace(),
        close=AsyncMock(),
    )
    from uuid import uuid4

    run_id = uuid4()
    sink = DaytonaRunStorage(
        sandbox,
        dispatcher=dispatcher,
        paths=VolumePaths.from_mount(),
        host_io=SimpleNamespace(),
        run_id=run_id,
    )
    await asyncio.to_thread(sink.volume_fs.write_bytes, f"/tmp/fleet/{run_id}/file.txt", b"data")
    sandbox.fs.upload_file.assert_awaited_once()
    dispatcher.sync_client_factory.assert_not_called()
    sink.close()
    sink.close()
    with pytest.raises(RuntimeError, match="Run storage is closed"):
        await sink.read(f"/tmp/fleet/{run_id}/file.txt", max_bytes=10)
    with pytest.raises(DaytonaAdapterError, match="bridge is closed"):
        await asyncio.to_thread(sink.sandbox.fs.upload_file, b"late", "file.txt")
    sandbox.close.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.usefixtures("broker")
async def test_slow_worker_lookup_keeps_application_loop_responsive():
    entered = threading.Event()
    release = threading.Event()
    service = LocalSDKInterpreter()
    client = MagicMock()

    def lookup(_identity):
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test did not release worker lookup")
        return SimpleNamespace(code_interpreter=service)

    client.get.side_effect = lookup
    dispatcher = SyncBridgeDispatcher()
    dispatcher.sync_client_factory = lambda: client
    sandbox = SimpleNamespace(id="root", fs=SimpleNamespace(), process=SimpleNamespace(), code_interpreter=service)
    view = sync_sandbox(sandbox, asyncio.get_running_loop(), dispatcher)
    client.get.assert_not_called()
    backend = sandbox_backend(sandbox, loop=asyncio.get_running_loop(), dispatcher=dispatcher)
    task = asyncio.create_task(asyncio.to_thread(backend.run, "value = 42"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        ticked = asyncio.Event()
        asyncio.get_running_loop().call_soon(ticked.set)
        await asyncio.wait_for(ticked.wait(), 0.5)
        assert not task.done()
    finally:
        release.set()
        await task
        await asyncio.to_thread(backend.close)
        view.close()
    client._http_client.close.assert_called_once()


@pytest.mark.usefixtures("broker")
@pytest.mark.parametrize("failure", [ConnectionError("stream reset"), TimeoutError("client timed out")])
def test_client_side_run_code_failure_poisons_the_backend(failure):
    service = MagicMock()
    actions = []

    def run_code(code, **_kwargs):
        if "def SUBMIT" in code:
            return SimpleNamespace(stdout="", stderr="", error=None)
        actions.append(code)
        raise failure

    service.run_code.side_effect = run_code
    tainted = MagicMock()
    backend = sandbox_backend(SimpleNamespace(code_interpreter=service), mark_tainted=tainted)
    with pytest.raises(Exception, match=str(failure)):
        backend.run("value = 1")
    tainted.assert_called_once_with()
    with pytest.raises(DaytonaAdapterError) as poisoned:
        backend.run("value = 2")
    assert poisoned.value.cause_type == "InterpreterExecutionStateUnknown"
    assert len(actions) == 1
    backend.close()


@pytest.mark.usefixtures("broker")
def test_unbounded_action_sends_the_sdk_default_timeout():
    service = LocalSDKInterpreter()
    observed = []
    run_code = service.run_code

    def run(code, **kwargs):
        observed.append(kwargs["timeout"])
        return run_code(code, **kwargs)

    service.run_code = run
    backend = sandbox_backend(SimpleNamespace(code_interpreter=service), timeout_s=None)
    assert backend.run("value = 1").error is None
    assert observed == [None, None]
    backend.close()


def _worker_backend(service):
    client = MagicMock()
    client.get.return_value = SimpleNamespace(code_interpreter=service)
    dispatcher = SyncBridgeDispatcher()
    dispatcher.sync_client_factory = lambda: client
    return sandbox_backend(SimpleNamespace(id="root"), dispatcher=dispatcher), client


def test_worker_scope_closes_even_when_context_delete_fails(broker):
    service = LocalSDKInterpreter()
    backend, client = _worker_backend(service)
    backend.run("value = 1")
    service.delete_context = MagicMock(side_effect=RuntimeError("delete failed"))
    with pytest.raises(RuntimeError, match="delete failed"):
        backend.close()
    client._http_client.close.assert_called_once()
    broker.stop.assert_called_once_with(strict=True)


@pytest.mark.usefixtures("broker")
def test_missing_context_on_delete_is_settled():
    from daytona.common.errors import DaytonaNotFoundError

    service = LocalSDKInterpreter()
    backend, client = _worker_backend(service)
    backend.run("value = 1")
    service.delete_context = MagicMock(side_effect=DaytonaNotFoundError("context gone"))
    backend.close()
    assert backend._sdk_context is None
    client._http_client.close.assert_called_once()


def test_worker_scope_drops_its_client_even_when_a_close_step_fails():
    from fleet_rlm.daytona.interpreter import _SDKWorkerSandbox

    client = MagicMock()
    client._http_client.close.side_effect = RuntimeError("close failed")
    scope = _SDKWorkerSandbox("root", lambda: client)
    assert scope.fs is client.get.return_value.fs
    with pytest.raises(RuntimeError, match="close failed"):
        scope.close()
    assert scope._client is None and scope._sandbox is None
    scope.close()
    client._subscription_manager.shutdown.assert_called_once()


@pytest.mark.usefixtures("broker")
def test_context_prelude_and_capture_helper_are_installed_once_per_context():
    service = LocalSDKInterpreter()
    backend = sandbox_backend(SimpleNamespace(code_interpreter=service))
    assert backend.run("value = 41").error is None
    assert backend.run("print(value + 1)").stdout == "42\n"
    setup, *actions = service.calls
    assert "def _fleet_capture_action" in setup and "chdir('/workspace')" in setup
    assert len(actions) == 2
    for action in actions:
        assert "def _fleet_capture_action" not in action
        assert "chdir" not in action
        assert "_fleet_capture_action(" in action
    backend.close()
