"""Stress and adversarial edge cases for direct Daytona SDK integration.

Stress-tests:
1. Sync/async bridge concurrency, timeouts, cancellations, closed/stopped loops.
2. SUBMIT() payload parsing with unicode, special chars, deep nesting, and malformed markers.
3. Leak-free sandbox cleanup and absence confirmation semantics.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import (
    FINAL_OUTPUT_MARKER,
    DaytonaCodeInterpreter,
    SyncBridgeDispatcher,
    _sync_await,
    extract_final_payload,
    final_output_frame,
    sandbox_backend,
    sync_sandbox,
    validate_json_value,
)
from fleet_rlm.daytona.runtime import (
    AbsenceConfirmation,
    AbsenceProbeError,
    AbsenceTimeout,
    DaytonaAdmission,
    cleanup_child_runtime_async,
    confirm_absence,
)
from fleet_rlm.rlm.compat_3_3_1 import FinalOutput
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError

# ============================================================================
# Section 1: Sync/Async Bridge Stress Tests
# ============================================================================


class _ServingLoop:
    """Asyncio loop managed on a dedicated background thread."""

    def __init__(self, name: str = "stress-serving-loop") -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._main, name=name, daemon=True)

    def _main(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def __enter__(self) -> _ServingLoop:
        self.thread.start()
        assert self._ready.wait(timeout=5.0)
        return self

    def stop(self, *, close: bool = True, join: bool = True) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        if join:
            self.thread.join(timeout=5.0)
        if close and not self.loop.is_closed():
            self.loop.close()

    def __exit__(self, *_exc: object) -> None:
        if self.thread.is_alive():
            self.stop()
        elif not self.loop.is_closed():
            self.loop.close()


@contextlib.contextmanager
def _registered_bridge(name: str = "stress-bridge"):
    dispatcher = SyncBridgeDispatcher()
    with _ServingLoop(name) as server:
        dispatcher.set_loop(server.loop)
        try:
            yield server, dispatcher
        finally:
            dispatcher.clear_loop(server.loop)


class _SlowAsyncFS:
    """Simulates async filesystem operations with realistic async delays."""

    def __init__(self) -> None:
        self.call_count = 0
        self._lock = asyncio.Lock()

    async def download_file(self, path: str) -> bytes:
        async with self._lock:
            self.call_count += 1
        await asyncio.sleep(0.01)  # small async yield
        return f"content:{path}".encode()

    async def upload_file(self, _data: bytes, _path: str) -> None:
        async with self._lock:
            self.call_count += 1
        await asyncio.sleep(0.01)


def test_sync_bridge_massive_concurrency() -> None:
    """Stress test: 50 concurrent threads executing sync bridge operations."""
    fs = _SlowAsyncFS()
    sandbox_double = SimpleNamespace(fs=fs)

    with _registered_bridge() as (_server, dispatcher):
        bridge = sync_sandbox(sandbox_double, asyncio.new_event_loop(), dispatcher)
        num_workers = 50
        results: list[bytes | None] = [None] * num_workers
        errors: list[Exception | None] = [None] * num_workers

        def worker_fn(idx: int) -> None:
            try:
                results[idx] = bridge.fs.download_file(f"/file_{idx}.txt")
            except Exception as e:
                errors[idx] = e

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            list(executor.map(worker_fn, range(num_workers)))
        elapsed = time.perf_counter() - started

        assert all(err is None for err in errors), f"Encountered errors: {errors}"
        for i in range(num_workers):
            assert results[i] == f"content:/file_{i}.txt".encode()
        assert fs.call_count == num_workers
        assert elapsed < 10.0, f"Concurrent execution took too long: {elapsed}s"


def test_sync_bridge_timeout_cancels_background_task() -> None:
    """Verify that a deadline timeout raises TimeoutError and cancels the coroutine."""
    task_cancelled = threading.Event()

    async def slow_operation() -> str:
        try:
            await asyncio.sleep(2.0)
            return "completed"
        except asyncio.CancelledError:
            task_cancelled.set()
            raise

    with _registered_bridge() as (server, dispatcher):
        deadline = time.monotonic() + 0.1
        with pytest.raises(TimeoutError, match="exceeded its Turn deadline"):
            dispatcher.run(slow_operation(), deadline=deadline)

        # Allow cancelled task to process in the event loop
        server.thread.join(timeout=0.2)
        assert task_cancelled.is_set(), "Background coroutine was not cancelled upon timeout"


def test_sync_bridge_propagates_timeout_from_operation() -> None:
    """A provider TimeoutError is raised once instead of becoming a poll loop."""

    async def failed_operation() -> str:
        raise TimeoutError("provider request timed out")

    with _registered_bridge() as (_server, dispatcher):
        deadline = time.monotonic() + 0.5
        with pytest.raises(TimeoutError, match="provider request timed out"):
            dispatcher.run(failed_operation(), deadline=deadline)


def test_sync_bridge_expired_deadline_fails_fast() -> None:
    """Verify that an already-expired deadline fails fast without scheduling work."""
    task_started = threading.Event()

    async def operation() -> str:
        task_started.set()
        return "ok"

    with _registered_bridge() as (_server, dispatcher):
        past_deadline = time.monotonic() - 0.5
        with pytest.raises(TimeoutError, match="exceeded its Turn deadline"):
            dispatcher.run(operation(), deadline=past_deadline)

        assert not task_started.is_set(), "Operation was executed despite expired deadline"


def test_sync_bridge_check_authority_failure() -> None:
    """Verify check_authority hook aborts execution when authorization is revoked."""
    call_count = 0

    def fail_authority() -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise PermissionError("lease revoked")

    async def slow_operation() -> str:
        await asyncio.sleep(0.5)
        return "done"

    with _registered_bridge() as (_server, dispatcher), pytest.raises(PermissionError, match="lease revoked"):
        dispatcher.run(slow_operation(), check_authority=fail_authority)


def test_sync_bridge_closed_loop_cleanup_no_coroutine_leak() -> None:
    """Verify closed/missing service loop closes coroutine cleanly without warnings."""
    coro_closed = False

    async def my_coro() -> str:
        nonlocal coro_closed
        try:
            await asyncio.sleep(1.0)
            return "done"
        finally:
            coro_closed = True

    import inspect

    coro = my_coro()
    dispatcher = SyncBridgeDispatcher()
    # Loop is None
    with pytest.raises(DaytonaAdapterError) as exc_info:
        dispatcher.run(coro)
    assert "service loop is unavailable" in str(exc_info.value)
    assert inspect.getcoroutinestate(coro) == "CORO_CLOSED", (
        "Coroutine was not closed when service loop was unavailable"
    )


def test_sync_bridge_service_loop_stopped_mid_execution() -> None:
    """Verify that stopping the loop while waiting raises typed DaytonaAdapterError."""
    loop_stopped = threading.Event()

    async def waiting_coro() -> str:
        await asyncio.sleep(1.0)
        return "late"

    server = _ServingLoop("stopping-loop")
    dispatcher = SyncBridgeDispatcher()
    with server:
        dispatcher.set_loop(server.loop)

        def stop_after_delay() -> None:
            time.sleep(0.1)
            server.stop(close=False, join=False)
            loop_stopped.set()

        t = threading.Thread(target=stop_after_delay, daemon=True)
        t.start()

        with pytest.raises(DaytonaAdapterError) as exc_info:
            dispatcher.run(waiting_coro())
        assert "service loop stopped" in str(exc_info.value)
        t.join(timeout=2.0)
        server.loop.close()


def test_sync_bridge_direct_loop_reentrancy_fails_typed() -> None:
    """Calling synchronous bridge from the service loop itself fails fast."""
    with _registered_bridge() as (server, dispatcher):
        bridge = sync_sandbox(SimpleNamespace(fs=_SlowAsyncFS()), server.loop, dispatcher)

        async def inner_call() -> bytes:
            return bridge.fs.download_file("/reentrancy")

        future = asyncio.run_coroutine_threadsafe(inner_call(), server.loop)
        with pytest.raises(DaytonaAdapterError) as exc_info:
            future.result(timeout=2.0)
        assert exc_info.value.cause_type == "InterpreterThreadError"


def test_sync_bridge_non_awaitable_rejected() -> None:
    """_sync_await rejects non-awaitable values with InterpreterBridgeContractError."""
    from fleet_rlm.daytona.interpreter import _SyncBridgeLoop

    owner = _SyncBridgeLoop(caller_loop=None)
    with pytest.raises(DaytonaAdapterError) as exc_info:
        _sync_await("not_awaitable", owner)
    assert exc_info.value.cause_type == "InterpreterBridgeContractError"


def test_sync_bridge_custom_awaitable_supported() -> None:
    """Custom awaitables (implementing __await__) succeed through the bridge."""

    class CustomAwaitable:
        def __await__(self):
            async def _inner():
                return "custom_val"

            return _inner().__await__()

    with _registered_bridge() as (_server, dispatcher):
        result = dispatcher.run(CustomAwaitable())
        assert result == "custom_val"


# ============================================================================
# Section 2: SUBMIT() Payload Parsing & Adversarial Edge Cases
# ============================================================================


def test_submit_unicode_and_special_characters_roundtrip() -> None:
    """Stress test: SUBMIT payload with multi-byte unicode, emojis, control chars, RTL."""
    adversarial_payload = {
        "cjk": "中文测试 日本語テスト 한국어시험",
        "rtl": "مرحبا بالعالم שלום עולם \u202e RLO override",
        "emojis": "🚀🎉🧑‍💻👨‍👩‍👧‍👦🇦🇺🇺🇸",
        "whitespace": "line1\nline2\r\ntab\t\tzero_width\u200bnon_break\u00a0",
        "symbols": "∫∬∭∮ ∑∏√ ∞≠≈ ≤≥ ⊕⊗",
        "escapes": "quotes: \" ' ` and backslashes \\ \\\\ \\n",
        "null_byte": "null\u0000in\u0000string",
    }

    frame = final_output_frame(adversarial_payload)
    assert FINAL_OUTPUT_MARKER in frame

    # Embed in noisy terminal stream
    noisy_stdout = f"INFO: starting task\n{frame}\nDEBUG: finished task\n"
    extracted = extract_final_payload(noisy_stdout)
    assert extracted == adversarial_payload


def test_submit_deeply_nested_json() -> None:
    """Stress test: Deeply nested JSON structures (depth 50)."""
    current: dict[str, Any] = {"leaf": "value_at_depth_50", "numbers": list(range(20))}
    for depth in range(50):
        current = {"level": depth, "child": current, "items": [depth, str(depth)]}

    frame = final_output_frame(current)
    extracted = extract_final_payload(frame)
    assert extracted == current


def test_submit_large_payload() -> None:
    """Stress test: Large JSON payload (> 100 KB text content)."""
    large_text = "The quick brown fox jumps over the lazy dog. " * 3000  # ~135 KB
    payload = {
        "summary": "Massive context extraction test",
        "body": large_text,
        "metadata": {"size_bytes": len(large_text), "status": "success"},
    }
    frame = final_output_frame(payload)
    extracted = extract_final_payload(frame)
    assert extracted == payload


@pytest.mark.parametrize(
    "invalid_val",
    [
        {"nan": float("nan")},
        {"inf": float("inf")},
        {"neg_inf": float("-inf")},
    ],
)
def test_submit_validation_rejects_non_finite_floats(invalid_val: dict[str, Any]) -> None:
    """Strict validation: non-finite numbers must raise TypeError."""
    with pytest.raises(TypeError, match="non-finite number"):
        validate_json_value(invalid_val)


def test_submit_validation_rejects_non_string_keys() -> None:
    """Strict validation: dictionary keys must strictly be strings."""
    with pytest.raises(TypeError, match="non-string dict key"):
        validate_json_value({123: "value"})  # type: ignore[dict-item]


@pytest.mark.parametrize(
    "unsupported",
    [
        {"set": {1, 2, 3}},
        {"obj": object()},
        {"func": lambda x: x},
    ],
)
def test_submit_validation_rejects_unsupported_types(unsupported: dict[str, Any]) -> None:
    """Strict validation: non-JSON serializable types must raise TypeError."""
    with pytest.raises(TypeError, match="unsupported type"):
        validate_json_value(unsupported)


_b64_string_json = base64.b64encode(b'"simple string"').decode("ascii")


@pytest.mark.parametrize(
    ("corrupted_stdout", "scenario"),
    [
        (f"{FINAL_OUTPUT_MARKER}{FINAL_OUTPUT_MARKER}", "empty marker"),
        (f"{FINAL_OUTPUT_MARKER}eyJmb28iOiAx", "unclosed start marker"),
        (f"eyJmb28iOiAx{FINAL_OUTPUT_MARKER}", "missing start marker"),
        (f"{FINAL_OUTPUT_MARKER}!!!NOT_BASE64!!!{FINAL_OUTPUT_MARKER}", "invalid base64 characters"),
        (
            f"{FINAL_OUTPUT_MARKER}{base64.b64encode(b'plain unparseable text').decode('ascii')}{FINAL_OUTPUT_MARKER}",
            "base64 decodes to non-json",
        ),
        (
            f"{FINAL_OUTPUT_MARKER}{base64.b64encode(b'[1, 2, 3]').decode('ascii')}{FINAL_OUTPUT_MARKER}",
            "json is list not dict",
        ),
        (
            f"{FINAL_OUTPUT_MARKER}{_b64_string_json}{FINAL_OUTPUT_MARKER}",
            "json is string not dict",
        ),
        (
            f"{FINAL_OUTPUT_MARKER}{base64.b64encode(b'12345').decode('ascii')}{FINAL_OUTPUT_MARKER}",
            "json is int not dict",
        ),
        (
            f"{FINAL_OUTPUT_MARKER}{base64.b64encode(b'true').decode('ascii')}{FINAL_OUTPUT_MARKER}",
            "json is bool not dict",
        ),
    ],
)
def test_extract_final_payload_adversarial_malformed_cases(
    corrupted_stdout: str,
    scenario: str,
) -> None:
    """Verify that malformed or non-dict payloads return None safely without raising."""
    result = extract_final_payload(corrupted_stdout)
    assert result is None, f"Scenario '{scenario}' failed: expected None, got {result}"


def test_extract_final_payload_prefixed_marker_flaw() -> None:
    """Verify that an earlier unclosed marker in stdout does not obscure the valid frame."""
    valid_frame = final_output_frame({"answer": "42"})
    stdout_with_unclosed_marker_in_logs = (
        f"Model thinking: I need to call SUBMIT using {FINAL_OUTPUT_MARKER} format...\n"
        f"Executing code...\n"
        f"{valid_frame}\n"
        f"Execution finished."
    )

    extracted = extract_final_payload(stdout_with_unclosed_marker_in_logs)
    assert extracted == {"answer": "42"}, "Hardened extractor must recover the valid payload"


def test_interpreter_code_execution_when_code_prints_marker() -> None:
    """Verify that when executed code prints the marker before SUBMIT(), final output is parsed."""
    mock_code_interpreter = MagicMock()
    mock_code_interpreter.create_context.return_value = "ctx-1"

    valid_frame = final_output_frame({"answer": "42"})
    # Simulated stdout where user script logged something mentioning the marker name
    stdout_with_log = f"DEBUG: Setting up {FINAL_OUTPUT_MARKER} handler\n{valid_frame}\n"
    mock_code_interpreter.run_code.return_value = MagicMock(
        stdout=stdout_with_log,
        stderr="",
        error=None,
    )
    mock_sandbox = MagicMock()
    mock_sandbox.code_interpreter = mock_code_interpreter

    backend = sandbox_backend(mock_sandbox)
    interpreter = DaytonaCodeInterpreter(backend=backend)
    interpreter.start()

    res = interpreter.execute("print('__FLEET_FINAL_OUTPUT__')\nSUBMIT(answer='42')")
    assert isinstance(res, FinalOutput), "Result must be FinalOutput"
    assert getattr(res, "output", {}).get("answer") == "42"


# ============================================================================
# Section 3: Leak-Free Sandbox Cleanup & Absence Confirmation
# ============================================================================


class _ScriptedDeletionProbe:
    """Scriptable probe returning states to test absence confirmation."""

    def __init__(self, sequence: list[Any | None]) -> None:
        self._sequence = list(sequence)
        self.call_count = 0

    async def __call__(self, _sandbox_id: str) -> Any | None:
        self.call_count += 1
        if self._sequence:
            return self._sequence.pop(0)
        return None


@pytest.mark.asyncio
async def test_confirm_absence_state_transitions() -> None:
    """Test full progression: running -> destroying -> destroyed."""
    probe = _ScriptedDeletionProbe(
        [
            SimpleNamespace(state="running"),
            SimpleNamespace(state="destroying"),
            SimpleNamespace(state="destroyed"),
        ]
    )

    outcome = await confirm_absence(
        probe=probe,
        sandbox_id="sb-stress-1",
        timeout_s=5.0,
        poll_interval_s=0.01,
    )

    assert isinstance(outcome, AbsenceConfirmation)
    assert outcome.absent is True
    assert outcome.observations == ("running", "destroying", "destroyed")
    assert probe.call_count == 3


@pytest.mark.asyncio
async def test_confirm_absence_purged_none_confirms() -> None:
    """Test 404 (None returned by probe) confirms absence immediately."""
    probe = _ScriptedDeletionProbe(
        [
            SimpleNamespace(state="running"),
            SimpleNamespace(state="deleting"),
            None,  # 404 not found
        ]
    )

    outcome = await confirm_absence(
        probe=probe,
        sandbox_id="sb-stress-2",
        timeout_s=5.0,
        poll_interval_s=0.01,
    )

    assert isinstance(outcome, AbsenceConfirmation)
    assert outcome.absent is True
    assert outcome.observations == ("running", "deleting", "not_found")


@pytest.mark.asyncio
async def test_confirm_absence_provider_error_fails() -> None:
    """Test provider reporting 'error' or 'build_failed' terminates with AbsenceProbeError."""
    probe = _ScriptedDeletionProbe(
        [
            SimpleNamespace(state="running"),
            SimpleNamespace(state="error"),
        ]
    )

    outcome = await confirm_absence(
        probe=probe,
        sandbox_id="sb-stress-3",
        timeout_s=5.0,
        poll_interval_s=0.01,
    )

    assert isinstance(outcome, AbsenceProbeError)
    assert outcome.absent is False
    assert "provider error state: error" in outcome.error


@pytest.mark.asyncio
async def test_confirm_absence_timeout_when_stuck() -> None:
    """Test probe continuously reporting 'running' times out with AbsenceTimeout."""
    probe = _ScriptedDeletionProbe([SimpleNamespace(state="running")] * 100)

    clock_time = 0.0

    def fake_clock() -> float:
        nonlocal clock_time
        return clock_time

    async def fake_sleep(_duration: float) -> None:
        nonlocal clock_time
        clock_time += 1.0

    outcome = await confirm_absence(
        probe=probe,
        sandbox_id="sb-stress-4",
        timeout_s=3.0,
        poll_interval_s=1.0,
        clock=fake_clock,
        sleep=fake_sleep,
    )

    assert isinstance(outcome, AbsenceTimeout)
    assert outcome.absent is False
    assert outcome.last_state == "running"


@pytest.mark.asyncio
async def test_confirm_absence_transient_network_exception_handled() -> None:
    """Test probe raising a network exception returns AbsenceProbeError without crashing."""

    async def failing_probe(_sandbox_id: str) -> Any:
        raise ConnectionResetError("Daytona API gateway unavailable")

    outcome = await confirm_absence(
        probe=failing_probe,
        sandbox_id="sb-stress-5",
        timeout_s=5.0,
    )

    assert isinstance(outcome, AbsenceProbeError)
    assert outcome.absent is False
    assert "Daytona API gateway unavailable" in outcome.error


@pytest.mark.asyncio
async def test_confirm_absence_cancelled_error_propagates() -> None:
    """Verify that cancellation on the caller loop propagates immediately."""

    async def cancelling_probe(_sandbox_id: str) -> Any:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await confirm_absence(
            probe=cancelling_probe,
            sandbox_id="sb-stress-6",
            timeout_s=5.0,
        )


@pytest.mark.asyncio
async def test_child_runtime_cleanup_leak_free_on_failure() -> None:
    """Verify that unconfirmed child deletion keeps its admission fence."""
    admission = DaytonaAdmission(max_active_leases=1)
    permit = await admission.acquire(deadline=time.monotonic() + 5.0)
    assert admission._semaphore._value == 0

    mock_platform = MagicMock()
    mock_platform.delete = AsyncMock(side_effect=RuntimeError("Deletion failed on provider"))
    mock_platform.get = AsyncMock(return_value=SimpleNamespace(state="running"))

    with pytest.raises(ChildRuntimeCleanupError):
        await cleanup_child_runtime_async(
            sandbox_id="child-sb-leak-test",
            sandbox=SimpleNamespace(fs=MagicMock()),
            platform=mock_platform,
            permit=permit,
            mount_path="/workspace",
            confirm_timeout_s=0.1,
            confirm_poll_interval_s=0.01,
        )

    assert permit._released is False
    assert admission._semaphore._value == 0
    permit.release()


def test_interpreter_context_and_sandbox_tombstoning_on_close() -> None:
    """Verify DaytonaCodeInterpreter deletes context and tombstones sync sandbox on close."""
    mock_code_interpreter = MagicMock()
    mock_code_interpreter.create_context.return_value = "ctx-to-delete"
    mock_code_interpreter.delete_context = MagicMock()
    mock_code_interpreter.run_code.return_value = MagicMock(stdout="ok", stderr="", error=None)

    mock_sandbox = MagicMock()
    mock_sandbox.code_interpreter = mock_code_interpreter

    backend = sandbox_backend(mock_sandbox)
    interpreter = DaytonaCodeInterpreter(backend=backend)
    interpreter.start()

    interpreter.execute("print('test')")
    assert backend._interpreter_context == "ctx-to-delete"

    # Close interpreter
    interpreter.shutdown()

    # Verify delete_context was called
    mock_code_interpreter.delete_context.assert_called_once_with("ctx-to-delete")
    assert backend._interpreter_context is None

    # Verify executing again fails fast
    with pytest.raises(DaytonaAdapterError) as exc_info:
        interpreter.execute("print('after close')")
    assert "shut down" in str(exc_info.value)
