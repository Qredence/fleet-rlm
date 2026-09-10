"""Native adapter replay: context state, host tools, typed output and cleanup fences."""

import asyncio
import contextlib
import inspect
import io
import json
import threading
import time
from types import SimpleNamespace

import dspy
import pytest

from fleet_rlm.daytona.broker import SyncBridgeDispatcher, remote_submit_setup_code, sync_sandbox
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.daytona.native_interpreter import NativeInterpreterBackend
from fleet_rlm.rlm.compat_3_3_1 import is_final_output


class Service:
    def __init__(self):
        self.contexts = {}
        self.deleted = []

    async def create_context(self):
        context = SimpleNamespace(id=str(len(self.contexts)))
        self.contexts[context.id] = {}
        return context

    def run_code(self, code, *, context, on_stdout, on_stderr, on_error, timeout):
        assert timeout > 0
        stdout, stderr = io.StringIO(), io.StringIO()
        error = None
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exec(code, self.contexts[context.id])
            except Exception as exc:
                error = SimpleNamespace(name=type(exc).__name__, value=str(exc), traceback="")
        if stdout.getvalue():
            on_stdout(SimpleNamespace(output=stdout.getvalue()))
        if stderr.getvalue():
            on_stderr(SimpleNamespace(output=stderr.getvalue()))
        if error:
            on_error(error)
        return SimpleNamespace(stdout=stdout.getvalue(), stderr=stderr.getvalue(), error=error)

    def delete_context(self, context, *, request_timeout):
        assert request_timeout > 0
        self.deleted.append(context.id)
        self.contexts.pop(context.id, None)


class Gateway:
    def __init__(self, namespace):
        self.namespace = namespace
        self.tools = {}
        self.stopped = False

    def ensure_started(self):
        pass

    def register_tools(self, tools):
        self.tools = dict(tools)

    def submit_setup_code(self, fields):
        wrappers = "\n".join(
            f"def {name}(*args, **kwargs): return _host({name!r}, list(args), kwargs)" for name in self.tools
        )
        return wrappers + "\n" + remote_submit_setup_code(fields)

    def execute_with_callbacks(self, *, run_code, tool_executor, check_authority):
        check_authority()
        self.namespace["_host"] = tool_executor
        return run_code()

    def stop(self, *, strict):
        assert strict
        self.stopped = True
        return True


async def _backend(*, cap=4096, authority=lambda: True, binding_current=lambda: True, contain=None):
    service = Service()
    context = await service.create_context()
    gateway = Gateway(service.contexts[context.id])
    contained = []
    backend = NativeInterpreterBackend(
        service=service,
        context=context,
        gateway=gateway,
        deadline=time.monotonic() + 10,
        max_output_bytes=cap,
        contain=contain or (lambda: contained.append(True)),
        is_authorized=authority,
        is_binding_current=binding_current,
        cleanup_timeout_seconds=1,
    )
    return backend, service, gateway, contained


@pytest.mark.asyncio
async def test_fresh_context_preserves_iterations_and_typed_submit_with_host_tool():
    backend, service, gateway, contained = await _backend()
    interpreter = DaytonaCodeInterpreter(
        backend=backend,
        tools={"double": lambda value: value * 2},
        output_fields=[{"name": "answer", "type": "str", "required": True}],
    )
    assert interpreter.execute("x = double(value=21)\nprint(x)") == "42\n"
    assert interpreter.execute("print(x + offset)", variables={"offset": 1}) == "43\n"
    result = interpreter.execute("SUBMIT(answer=str(x))")
    assert is_final_output(result)
    assert backend.containment_required
    interpreter.shutdown()
    assert service.deleted == ["0"]
    assert gateway.stopped and contained == [True]
    assert not backend.containment_required
    interpreter.shutdown()
    assert contained == [True]


@pytest.mark.asyncio
async def test_replacement_context_does_not_retain_python_globals_but_keeps_volume_data():
    """A replacement generation starts fresh while durable Volume state survives."""
    volume: dict[str, str] = {}

    first, service, _, contained = await _backend()
    first_interpreter = DaytonaCodeInterpreter(
        backend=first,
        tools={"write_volume": lambda key, value: volume.__setitem__(key, value)},
    )
    first_interpreter.execute("ephemeral_only = 'must-not-survive'\nwrite_volume('checkpoint', 'durable-value')")
    first_interpreter.shutdown()
    assert contained == [True]

    second_context = await service.create_context()
    second_gateway = Gateway(service.contexts[second_context.id])
    second, _, _, second_contained = await _backend()
    # Reuse the service's newly-created context with a fresh backend; the
    # in-memory dictionary represents the durable Volume mounted by both
    # provider generations, not interpreter state.
    second._service = service
    second._context = second_context
    second._gateway = second_gateway
    second_interpreter = DaytonaCodeInterpreter(
        backend=second,
        tools={"read_volume": lambda key: volume[key]},
    )
    assert second_interpreter.execute("print('ephemeral_only' in globals())") == "False\n"
    assert second_interpreter.execute("print(read_volume('checkpoint'))") == "durable-value\n"
    second_interpreter.shutdown()
    assert second_contained == [True]


@pytest.mark.asyncio
async def test_stock_dspy_rlm_uses_native_semantic_callback_in_caller_owned_context():
    backend, service, _, contained = await _backend()
    interpreter = DaytonaCodeInterpreter(
        backend=backend,
        output_fields=[{"name": "answer", "type": "str", "required": True}],
    )
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "query selected input", "code": "SUBMIT(answer=llm_query('selected'))"},
            {"reasoning": "report failure", "code": "SUBMIT(answer='fallback')"},
        ],
        adapter=dspy.JSONAdapter(),
    )
    sub = dspy.utils.DummyLM([{"answer": "semantic evidence"}], adapter=dspy.JSONAdapter())
    program = dspy.RLM("prompt -> answer", sub_lm=sub, max_iters=2, max_llm_calls=1)
    with dspy.context(lm=root, adapter=dspy.JSONAdapter()):
        prediction = await program.acall(interpreter, prompt="test")
    assert json.loads(prediction.answer) == {"answer": "semantic evidence"}
    assert len(prediction.trajectory) == 1
    assert len(sub.history) == 1
    assert len(service.contexts) == 1
    assert not service.deleted and not contained
    interpreter.shutdown()


@pytest.mark.asyncio
async def test_native_host_callback_can_schedule_child_through_composition_bridge():
    """The native context may synchronously wait on an async child callback.

    The child operation runs on the composition service loop, not the loop
    currently executing the native interpreter action. This is the nested
    root-code -> host-callback -> child -> root-resume shape that previously
    deadlocked when bridges captured the parked caller loop.
    """

    class ChildProcess:
        async def code_run(self, code: str) -> SimpleNamespace:
            await asyncio.sleep(0)
            return SimpleNamespace(result=f"child:{code}")

    class ServingLoop:
        def __init__(self) -> None:
            self.loop = asyncio.new_event_loop()
            self.ready = threading.Event()
            self.thread = threading.Thread(target=self._serve, daemon=True)

        def _serve(self) -> None:
            asyncio.set_event_loop(self.loop)
            self.ready.set()
            self.loop.run_forever()

        def start(self) -> None:
            self.thread.start()
            assert self.ready.wait(timeout=2)

        def close(self) -> None:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)
            self.loop.close()

    service_loop = ServingLoop()
    service_loop.start()
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(service_loop.loop)
    child_bridge = sync_sandbox(
        SimpleNamespace(process=ChildProcess()),
        asyncio.get_running_loop(),
        dispatcher,
    )

    def schedule_child_sync(label: str) -> str:
        # A real host Tool may itself be async. Resolve it through the same
        # composition-owned bridge used by Daytona SDK views.
        child = child_bridge.process.code_run(label)
        return str(child.result)

    backend, service, gateway, contained = await _backend()
    interpreter = DaytonaCodeInterpreter(
        backend=backend,
        tools={"schedule_child": schedule_child_sync},
        output_fields=[{"name": "answer", "type": "str", "required": True}],
    )
    try:
        interpreter._ensure_bindings()
        assert tuple(inspect.signature(gateway.tools["schedule_child"]).parameters) == ("label",)
        # DSPy calls a synchronous interpreter from its worker thread; keep
        # the event-loop thread free to represent that production boundary.
        result = await asyncio.to_thread(
            interpreter.execute,
            "value = schedule_child('probe')\nSUBMIT(answer=value)",
        )
        assert is_final_output(result)
        assert result.output == {"answer": "child:probe"}
        assert service.contexts["0"]
        assert not service.deleted
    finally:
        interpreter.shutdown()
        dispatcher.clear_loop(service_loop.loop)
        service_loop.close()
    assert gateway.stopped and contained == [True]


@pytest.mark.asyncio
async def test_contexts_do_not_share_variables():
    first, *_ = await _backend()
    second, *_ = await _backend()
    first.run("x = 42")
    result = second.run("print(x)")
    assert result.error_category == "NameError"
    first.close()
    second.close()


@pytest.mark.asyncio
async def test_output_overflow_fences_further_execution_and_requires_containment():
    backend, _, _, contained = await _backend(cap=8)
    with pytest.raises(DaytonaAdapterError, match="output limit"):
        backend.run("print('é' * 5)")
    with pytest.raises(DaytonaAdapterError, match="authority unavailable"):
        backend.run("print('late')")
    assert backend.containment_required and not contained
    backend.close()
    assert contained == [True]


@pytest.mark.asyncio
async def test_binding_generation_revocation_blocks_result_publication_and_future_callbacks():
    """A replacement generation fences the old native action at its result boundary."""
    current = True
    backend, service, gateway, contained = await _backend(binding_current=lambda: current)

    original_run_code = service.run_code

    def revoke_after_execution(*args, **kwargs):
        nonlocal current
        result = original_run_code(*args, **kwargs)
        current = False
        return result

    service.run_code = revoke_after_execution  # type: ignore[method-assign]
    interpreter = DaytonaCodeInterpreter(
        backend=backend,
        tools={"double": lambda value: value * 2},
        output_fields=[{"name": "answer", "type": "str", "required": True}],
    )
    with pytest.raises(DaytonaAdapterError, match="authority unavailable"):
        interpreter.execute("SUBMIT(answer='must-not-publish')")
    with pytest.raises(DaytonaAdapterError, match="authority unavailable"):
        interpreter.execute("double(1)")
    assert not contained
    interpreter.shutdown()
    assert service.deleted == ["0"]
    assert gateway.stopped


@pytest.mark.asyncio
async def test_binding_revocation_during_native_sub_lm_callback_fences_and_contains():
    """A callback that loses its generation cannot resume or publish output."""
    current = True

    def revoke_during_sub_lm(_prompt: str) -> str:
        nonlocal current
        current = False
        return "late-sub-lm-result"

    backend, service, gateway, contained = await _backend(binding_current=lambda: current)
    interpreter = DaytonaCodeInterpreter(
        backend=backend,
        tools={"llm_query": revoke_during_sub_lm},
        output_fields=[{"name": "answer", "type": "str", "required": True}],
    )
    with pytest.raises(DaytonaAdapterError, match="authority unavailable"):
        interpreter.execute("answer = llm_query('prompt')\nSUBMIT(answer=answer)")

    # The failed callback cannot be reused, and the regular owner cleanup
    # still contains the complete native sandbox.
    with pytest.raises(DaytonaAdapterError, match="authority unavailable"):
        interpreter.execute("llm_query('again')")
    interpreter.shutdown()
    assert contained == [True]
    assert service.deleted == ["0"]
    assert gateway.stopped


@pytest.mark.asyncio
async def test_authority_revocation_prevents_host_callback_and_execution():
    backend, service, _, _ = await _backend(authority=lambda: False)
    with pytest.raises(DaytonaAdapterError, match="authority unavailable"):
        backend.run("x = 42")
    assert "x" not in service.contexts["0"]
    backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["stdout", "stderr", "error"])
async def test_completed_output_is_bounded_when_transport_omits_callbacks(monkeypatch, channel):
    backend, service, _, contained = await _backend(cap=8)
    result = SimpleNamespace(stdout="", stderr="", error=None)
    if channel == "error":
        result.error = SimpleNamespace(name="Error", value="", traceback="é" * 5)
    else:
        setattr(result, channel, "é" * 5)
    monkeypatch.setattr(service, "run_code", lambda *_args, **_kwargs: result)
    with pytest.raises(DaytonaAdapterError, match="output limit"):
        backend.run("pass")
    with pytest.raises(DaytonaAdapterError, match="authority unavailable"):
        backend.run("pass")
    assert backend.containment_required and not contained
    backend.close()
    assert contained == [True]


@pytest.mark.asyncio
async def test_completed_output_check_does_not_double_count_streamed_bytes():
    backend, _, _, _ = await _backend(cap=8)
    assert backend.run("print('1234567')").stdout == "1234567\n"
    backend.close()


@pytest.mark.asyncio
async def test_failed_containment_stays_owned_and_retryable():
    attempts = []

    def contain():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("containment pending")

    backend, service, gateway, _ = await _backend(contain=contain)
    with pytest.raises(RuntimeError, match="containment pending"):
        backend.close()
    assert backend.containment_required and not gateway.stopped
    backend.close()
    assert not backend.containment_required
    assert service.deleted == ["0"]
