"""Native adapter replay: context state, host tools, typed output and cleanup fences."""

import contextlib
import io
import json
import time
from types import SimpleNamespace

import dspy
import pytest

from fleet_rlm.daytona.broker import remote_submit_setup_code
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


async def _backend(*, cap=4096, authority=lambda: True, contain=None):
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
async def test_authority_revocation_prevents_host_callback_and_execution():
    backend, service, _, _ = await _backend(authority=lambda: False)
    with pytest.raises(DaytonaAdapterError, match="authority unavailable"):
        backend.run("x = 42")
    assert "x" not in service.contexts["0"]
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
