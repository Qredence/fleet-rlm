"""Opt-in Phase 3 proof for the caller-owned Daytona native interpreter path.

The lane intentionally keeps the production policy on ``legacy``.  It exercises
the explicit feasibility seam with the same pinned Daytona snapshot and records
bounded evidence for the native context, the existing preview/polling broker,
the composition-owned sync bridge, and the cleanup decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from dotenv import load_dotenv

from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker, SyncBridgeDispatcher, sync_sandbox
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.native_interpreter import NativeInterpreterBackend
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, build_daytona_client
from fleet_rlm.daytona.provisioning import sandbox_spec_from_settings

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(900)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_ENV = "FLEET_PHASE3_NATIVE_EVIDENCE_PATH"
_REQUIRED_DSPY = "3.3.1"
_REQUIRED_DAYTONA = "0.210.0"


def _load_live_settings() -> Any:
    load_dotenv(_REPO_ROOT / ".env", override=False)
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for the Phase 3 Daytona feasibility lane")
    settings = load_runtime_settings()
    if settings.daytona_api_key is None:
        pytest.fail("Phase 3 feasibility lane requires the configured Daytona API key")
    if importlib.metadata.version("dspy") != _REQUIRED_DSPY:
        pytest.fail("Phase 3 feasibility lane requires the pinned DSPy release")
    if importlib.metadata.version("daytona") != _REQUIRED_DAYTONA:
        pytest.fail("Phase 3 feasibility lane requires the pinned Daytona release")
    return settings


def _write_receipt(payload: dict[str, object]) -> None:
    raw_path = os.environ.get(_EVIDENCE_ENV)
    if not raw_path:
        return
    path = Path(raw_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


async def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if not callable(close):
        return
    with contextlib.suppress(BaseException):
        result = close()
        if hasattr(result, "__await__"):
            await result


async def _delete_sandbox(platform: LiveDaytonaPlatform, sandbox: Any) -> bool:
    sandbox_id = str(getattr(sandbox, "id", ""))
    with contextlib.suppress(BaseException):
        await platform.stop(sandbox_id, timeout=60, force=True)
    with contextlib.suppress(BaseException):
        await platform.delete(sandbox)
    with contextlib.suppress(BaseException):
        return await platform.get(sandbox_id) is None
    return False


async def _native_interpreter(
    sandbox: Any,
    context: Any,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher,
    *,
    tools: dict[str, Any],
    authorized: Any,
    contained: list[bool],
) -> tuple[DaytonaCodeInterpreter, DaytonaHttpToolBroker]:
    view = sync_sandbox(sandbox, loop, dispatcher)
    gateway = DaytonaHttpToolBroker(sandbox=view)
    backend = NativeInterpreterBackend(
        service=view.code_interpreter,
        context=context,
        gateway=gateway,
        deadline=time.monotonic() + 180,
        max_output_bytes=20_000,
        contain=lambda: contained.append(True),
        is_authorized=authorized,
        cleanup_timeout_seconds=45,
    )
    return (
        DaytonaCodeInterpreter(
            backend=backend,
            tools=tools,
            output_fields=[{"name": "answer", "type": "str", "required": True}],
        ),
        gateway,
    )


@pytest.mark.asyncio
async def test_phase3_daytona_native_feasibility() -> None:
    """Exercise native/broker parity, nested callbacks, isolation and cleanup."""
    settings = _load_live_settings()
    client = build_daytona_client(settings)
    platform = LiveDaytonaPlatform(client, sandbox_spec_from_settings(settings))
    loop = asyncio.get_running_loop()
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(loop)
    sandboxes: list[Any] = []
    contexts: list[tuple[Any, Any]] = []
    timings: dict[str, int] = {}
    assertions: dict[str, bool] = {}
    native_process_contained = False
    native_process_quarantined = False
    cleanup_confirmed = True
    native_interpreter: DaytonaCodeInterpreter | None = None
    broker_interpreter: DaytonaCodeInterpreter | None = None
    cancellation_interpreter: DaytonaCodeInterpreter | None = None
    detached_interpreter: DaytonaCodeInterpreter | None = None
    cleanup_started = time.perf_counter()

    async def create(label: str) -> Any:
        started = time.perf_counter()
        sandbox = await platform.create(with_volume=False, ephemeral=True)
        timings[f"{label}_sandbox_acquisition_ms"] = _elapsed_ms(started)
        sandboxes.append(sandbox)
        return sandbox

    try:
        root = await create("native")
        child = await create("nested_child")
        native_context_started = time.perf_counter()
        root_context = await root.code_interpreter.create_context(cwd="/home/daytona", request_timeout=60)
        timings["native_context_creation_ms"] = _elapsed_ms(native_context_started)
        child_context = await child.code_interpreter.create_context(cwd="/home/daytona", request_timeout=60)
        contexts.extend(((root, root_context), (child, child_context)))

        child_view = sync_sandbox(child, loop, dispatcher)

        def run_child(label: str) -> str:
            if label != "nested":
                raise ValueError("unexpected nested label")
            result = child_view.code_interpreter.run_code(
                "print('child-complete:nested')",
                context=child_context,
            )
            if getattr(result, "error", None):
                raise RuntimeError("nested child execution failed")
            return str(getattr(result, "stdout", "")).strip()

        native_contained: list[bool] = []
        native_interpreter, native_gateway = await _native_interpreter(
            root,
            root_context,
            loop,
            dispatcher,
            tools={"read_value": lambda: "42", "run_child": run_child},
            authorized=lambda: True,
            contained=native_contained,
        )
        bootstrap_started = time.perf_counter()
        await asyncio.to_thread(native_interpreter._ensure_bindings)
        timings["native_bootstrap_ms"] = _elapsed_ms(bootstrap_started)
        first_started = time.perf_counter()
        nested = await asyncio.to_thread(
            native_interpreter.execute,
            "value = run_child('nested')\nSUBMIT(answer=value)",
        )
        timings["native_first_action_ms"] = _elapsed_ms(first_started)
        assert getattr(nested, "output", None) == {"answer": "child-complete:nested"}
        # Capture the broker's host-tool round-trip while the nested action is
        # still the most recent execution.  A later no-tool action legitimately
        # reports zero tool time and must not erase this Phase 3 measurement.
        native_stats = dict(native_gateway.last_execution_stats)
        timings["native_host_tool_round_trip_ms"] = int(
            native_stats.get("tool_execution_ms", 0) + native_stats.get("result_post_ms", 0)
        )
        subsequent_started = time.perf_counter()
        subsequent = await asyncio.to_thread(native_interpreter.execute, "print('native-subsequent')")
        timings["native_subsequent_action_ms"] = _elapsed_ms(subsequent_started)
        assert "native-subsequent" in str(subsequent)
        preview_host = str(getattr(native_gateway, "_broker_url", ""))
        preview_hostname = (urlparse(preview_host).hostname or "").lower()
        assertions.update(
            {
                "native_nested_root_child_resume": True,
                "native_typed_submit": True,
                "native_preview_transport": preview_hostname not in {"", "localhost", "127.0.0.1", "::1"},
                "native_context_is_explicit": True,
            }
        )
        await asyncio.to_thread(native_interpreter.shutdown, strict_broker_cleanup=True)
        native_interpreter = None

        broker_sandbox = await create("broker")
        broker_interpreter = DaytonaCodeInterpreter(
            backend=sandbox_backend(broker_sandbox, loop=loop, dispatcher=dispatcher),
            tools={"read_value": lambda: "42"},
            output_fields=[{"name": "answer", "type": "str", "required": True}],
        )
        broker_first_started = time.perf_counter()
        broker_result = await asyncio.to_thread(
            broker_interpreter.execute,
            "value = read_value()\nSUBMIT(answer=value)",
        )
        timings["broker_first_action_ms"] = _elapsed_ms(broker_first_started)
        assert getattr(broker_result, "output", None) == {"answer": "42"}
        broker_subsequent_started = time.perf_counter()
        broker_subsequent = await asyncio.to_thread(broker_interpreter.execute, "print('broker-subsequent')")
        timings["broker_subsequent_action_ms"] = _elapsed_ms(broker_subsequent_started)
        assert "broker-subsequent" in str(broker_subsequent)
        await asyncio.to_thread(broker_interpreter.shutdown, strict_broker_cleanup=True)
        broker_interpreter = None
        assertions["broker_typed_submit"] = True
        assertions["native_broker_output_contract_parity"] = True

        cancel_sandbox = await create("cancel")
        cancel_context = await cancel_sandbox.code_interpreter.create_context(cwd="/home/daytona", request_timeout=60)
        contexts.append((cancel_sandbox, cancel_context))
        authorized = {"value": True}

        def slow_host_tool() -> str:
            time.sleep(0.25)
            authorized["value"] = False
            return "late"

        cancellation_interpreter, _ = await _native_interpreter(
            cancel_sandbox,
            cancel_context,
            loop,
            dispatcher,
            tools={"slow_host_tool": slow_host_tool},
            authorized=lambda: bool(authorized["value"]),
            contained=[],
        )
        cancellation_started = time.perf_counter()
        with pytest.raises(Exception, match=r"authority|cancel|lifecycle"):
            await asyncio.to_thread(cancellation_interpreter.execute, "print(slow_host_tool())")
        timings["native_host_callback_cancellation_ms"] = _elapsed_ms(cancellation_started)
        assertions["native_host_callback_authority_loss_blocks_publication"] = True
        await asyncio.to_thread(cancellation_interpreter.shutdown, strict_broker_cleanup=True)
        cancellation_interpreter = None

        detached_sandbox = await create("detached")
        detached_context = await detached_sandbox.code_interpreter.create_context(
            cwd="/home/daytona", request_timeout=60
        )
        contexts.append((detached_sandbox, detached_context))
        marker = f"/tmp/fleet-phase3-detached-{uuid4().hex}.txt"
        detached_interpreter, _ = await _native_interpreter(
            detached_sandbox,
            detached_context,
            loop,
            dispatcher,
            tools={},
            authorized=lambda: True,
            contained=[],
        )
        detached_child_code = f"import time; time.sleep(2); open({marker!r}, 'w').write('late')"
        detached_code = (
            "import subprocess, sys\n"
            f"subprocess.Popen([sys.executable, '-c', {detached_child_code!r}], start_new_session=True)\n"
            "print('spawned')"
        )
        await asyncio.to_thread(detached_interpreter.execute, detached_code)
        await asyncio.to_thread(detached_interpreter.shutdown, strict_broker_cleanup=True)
        detached_interpreter = None
        await asyncio.sleep(3)
        marker_result = await detached_sandbox.process.code_run(
            f"import os; print(os.path.exists({marker!r}))",
            timeout=10,
        )
        native_process_contained = str(getattr(marker_result, "result", "")).strip() == "False"
        if not native_process_contained:
            # The marker proves that the native context did not contain the
            # detached process. Stop/delete this disposable provider sandbox
            # immediately; do not reuse an uncertain native root.
            native_process_quarantined = await _delete_sandbox(platform, detached_sandbox)
        assertions["detached_process_probe_recorded"] = True
        assertions["uncertain_native_process_is_quarantined"] = native_process_contained or native_process_quarantined

        isolation_started = time.perf_counter()
        isolated_a, isolated_b = await asyncio.gather(create("isolation_a"), create("isolation_b"))
        outputs = await asyncio.gather(
            isolated_a.process.code_run("print('session-a')", timeout=20),
            isolated_b.process.code_run("print('session-b')", timeout=20),
        )
        timings["different_session_concurrency_ms"] = _elapsed_ms(isolation_started)
        assertions["different_sessions_have_distinct_sandboxes"] = str(isolated_a.id) != str(isolated_b.id)
        assertions["different_sessions_execute_concurrently"] = {
            str(getattr(outputs[0], "result", "")).strip(),
            str(getattr(outputs[1], "result", "")).strip(),
        } == {"session-a", "session-b"}

        assertions["native_detached_process_contained"] = native_process_contained
    finally:
        cleanup_started = time.perf_counter()
        for interpreter in (native_interpreter, broker_interpreter, cancellation_interpreter, detached_interpreter):
            if interpreter is not None:
                with contextlib.suppress(BaseException):
                    await asyncio.to_thread(interpreter.shutdown, strict_broker_cleanup=True)
        # Context deletion is explicit for each native feasibility owner; a
        # missing context after provider teardown is already a successful end.
        for sandbox, context in contexts:
            with contextlib.suppress(BaseException):
                await sandbox.code_interpreter.delete_context(context, request_timeout=45)
        for sandbox in sandboxes:
            cleanup_confirmed = (await _delete_sandbox(platform, sandbox)) and cleanup_confirmed
        timings["cleanup_ms"] = _elapsed_ms(cleanup_started)
        dispatcher.clear_loop(loop)
        await _close_client(client)

    assertions["all_disposable_sandboxes_absent"] = cleanup_confirmed
    # Native production remains a no-go whenever the detached process marker
    # survives context deletion. The broker remains the proven compatibility
    # path even when this particular provider generation happens to contain it.
    native_go = native_process_contained and assertions.get("all_disposable_sandboxes_absent", False)
    required_assertions = (
        "native_nested_root_child_resume",
        "native_typed_submit",
        "native_preview_transport",
        "native_context_is_explicit",
        "broker_typed_submit",
        "native_broker_output_contract_parity",
        "native_host_callback_authority_loss_blocks_publication",
        "detached_process_probe_recorded",
        "uncertain_native_process_is_quarantined",
        "different_sessions_have_distinct_sandboxes",
        "different_sessions_execute_concurrently",
        "all_disposable_sandboxes_absent",
    )
    passed = all(assertions.get(name, False) for name in required_assertions)
    _write_receipt(
        {
            "schema": "fleet.phase3-daytona-native-feasibility/v1",
            "versions": {
                "python": sys.version.split()[0],
                "dspy": importlib.metadata.version("dspy"),
                "daytona": importlib.metadata.version("daytona"),
            },
            "transport": {
                "native_context": True,
                "host_callback": "daytona_preview_http_poll",
                "loopback_host_assumption": False,
                "composition_bridge": True,
            },
            "timings_ms": timings,
            "assertions": assertions,
            "containment": {
                "detached_process_contained": native_process_contained,
                "quarantined_when_uncertain": native_process_quarantined,
                "all_disposable_sandboxes_absent": cleanup_confirmed,
            },
            "go_no_go": {
                "native_production": native_go,
                "retained_broker_compatibility": True,
                "reason": "detached_process_containment_uncertified",
            },
            "passed": passed,
        }
    )
    assert passed, "Phase 3 native feasibility assertions did not all pass"
