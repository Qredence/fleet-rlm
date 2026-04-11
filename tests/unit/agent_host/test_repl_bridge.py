from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from fleet_rlm.agent_host.execution_events import HostedExecutionEvent
from fleet_rlm.agent_host.repl_bridge import ReplHookBridge


class _RecordingLifecycle:
    def __init__(self) -> None:
        self.run_completed = False
        self.emitted_steps: list[Any] = []
        self.persisted_steps: list[Any] = []
        self.persistence_errors: list[Exception] = []
        self.step_persisted = asyncio.Event()
        self.error_recorded = asyncio.Event()

    async def emit_step(self, step: Any) -> None:
        self.emitted_steps.append(step)

    async def persist_step(self, step: Any) -> None:
        self.persisted_steps.append(step)
        self.step_persisted.set()

    def record_persistence_error(self, exc: Exception) -> None:
        self.persistence_errors.append(exc)
        self.error_recorded.set()


class _InterpreterHookStepBuilder:
    def from_interpreter_hook(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": "step-1", "payload": payload}


class _FailingLifecycle(_RecordingLifecycle):
    async def persist_step(self, step: Any) -> None:
        raise RuntimeError("persist failed")


def test_repl_hook_bridge_routing_and_chaining() -> None:
    async def scenario() -> None:
        lifecycle = _RecordingLifecycle()
        previous_calls: list[dict[str, Any]] = []
        routed_events: list[HostedExecutionEvent] = []
        interpreter = SimpleNamespace(
            execution_event_callback=previous_calls.append,
            current_runtime_metadata=lambda: {
                "sandbox_id": "sandbox-1",
                "workspace_path": "/home/daytona/workspace",
                "volume_name": "memory-volume",
            },
            _session=SimpleNamespace(context_id="ctx-1"),
        )

        def enqueue_nonblocking(
            queue: asyncio.Queue[dict[str, Any] | None],
            step: dict[str, Any],
        ) -> bool:
            queue.put_nowait(step)
            return True

        bridge = ReplHookBridge(
            ws_loop=asyncio.get_running_loop(),
            lifecycle=cast(Any, lifecycle),
            step_builder=cast(Any, _InterpreterHookStepBuilder()),
            interpreter=interpreter,
            enqueue_nonblocking=enqueue_nonblocking,
            route_event=routed_events.append,
        )

        payload = {
            "phase": "start",
            "timestamp": 123.0,
            "code_hash": "abc123",
            "code_preview": "print('hello')",
        }

        await bridge.start()
        interpreter.execution_event_callback(payload)
        await asyncio.wait_for(lifecycle.step_persisted.wait(), timeout=1.0)
        await bridge.stop()

        assert previous_calls == [payload]
        assert lifecycle.emitted_steps == [{"id": "step-1", "payload": payload}]
        assert lifecycle.persisted_steps == [{"id": "step-1", "payload": payload}]
        assert lifecycle.persistence_errors == []

        assert len(routed_events) == 1
        assert routed_events[0].payload == payload
        assert routed_events[0].state_refs.sandbox_id == "sandbox-1"
        assert routed_events[0].state_refs.volume_name == "memory-volume"
        assert routed_events[0].state_refs.workspace_path == "/home/daytona/workspace"
        assert routed_events[0].state_refs.interpreter_context_id == "ctx-1"

    asyncio.run(scenario())


def test_repl_hook_bridge_records_persistence_errors() -> None:
    async def scenario() -> None:
        lifecycle = _FailingLifecycle()
        interpreter = SimpleNamespace(execution_event_callback=None)

        def enqueue_nonblocking(
            queue: asyncio.Queue[dict[str, Any] | None],
            step: dict[str, Any],
        ) -> bool:
            queue.put_nowait(step)
            return True

        bridge = ReplHookBridge(
            ws_loop=asyncio.get_running_loop(),
            lifecycle=cast(Any, lifecycle),
            step_builder=cast(Any, _InterpreterHookStepBuilder()),
            interpreter=interpreter,
            enqueue_nonblocking=enqueue_nonblocking,
        )

        await bridge.start()
        interpreter.execution_event_callback(
            {
                "phase": "start",
                "timestamp": 123.0,
                "code_hash": "abc123",
                "code_preview": "print('hello')",
            }
        )
        await asyncio.wait_for(lifecycle.error_recorded.wait(), timeout=1.0)
        await bridge.stop()

        assert len(lifecycle.persistence_errors) == 1
        assert str(lifecycle.persistence_errors[0]) == "persist failed"

    asyncio.run(scenario())
