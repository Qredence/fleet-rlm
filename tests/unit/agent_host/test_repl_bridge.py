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

    async def emit_step(self, step: Any) -> None:
        self.emitted_steps.append(step)

    async def persist_step(self, step: Any) -> None:
        self.persisted_steps.append(step)

    def record_persistence_error(self, exc: Exception) -> None:
        self.persistence_errors.append(exc)


class _InterpreterHookStepBuilder:
    def from_interpreter_hook(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": "step-1", "payload": payload}


def test_repl_hook_bridge_routes_hosted_execution_refs_and_chains_previous_hook() -> (
    None
):
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
        await asyncio.sleep(0)
        await asyncio.sleep(0)
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
