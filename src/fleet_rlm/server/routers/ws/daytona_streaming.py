"""Daytona-backed websocket streaming adapter for the experimental pilot."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from fleet_rlm.daytona_rlm import DaytonaRLMRunner, RolloutBudget
from fleet_rlm.daytona_rlm.types import DaytonaRunCancelled
from fleet_rlm.models import StreamEvent

from .helpers import _try_send_json


def _event_dict(event: StreamEvent) -> dict[str, Any]:
    return {
        "kind": event.kind,
        "text": event.text,
        "payload": event.payload,
        "timestamp": event.timestamp.isoformat(),
        "version": 2,
        "event_id": str(uuid.uuid4()),
    }


def _render_final_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:
        return str(value)


async def _emit_stream_event(websocket: WebSocket, event: StreamEvent) -> bool:
    return await _try_send_json(
        websocket, {"type": "event", "data": _event_dict(event)}
    )


async def run_daytona_streaming_turn(
    *,
    websocket: WebSocket,
    planner_lm: Any,
    message: str,
    repo_url: str,
    repo_ref: str | None,
    max_depth: int | None,
    batch_concurrency: int | None,
    cancel_check: Any,
) -> None:
    """Run the experimental Daytona pilot and stream progress over websocket."""

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def enqueue_event(event: StreamEvent) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run_blocking() -> None:
        try:
            budget = RolloutBudget(
                max_depth=max_depth
                if max_depth is not None
                else RolloutBudget().max_depth,
                batch_concurrency=(
                    batch_concurrency
                    if batch_concurrency is not None
                    else RolloutBudget().batch_concurrency
                ),
            )
            runner = DaytonaRLMRunner(
                lm=planner_lm,
                budget=budget,
                event_callback=enqueue_event,
                cancel_check=cancel_check,
            )
            result_box["result"] = runner.run(
                repo=repo_url,
                ref=repo_ref,
                task=message,
            )
        except BaseException as exc:  # pragma: no cover - surfaced to async path
            error_box["error"] = exc

    task = asyncio.create_task(asyncio.to_thread(run_blocking))

    while True:
        if task.done() and queue.empty():
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            continue
        if not await _emit_stream_event(websocket, event):
            break

    await task

    if "error" in error_box:
        exc = error_box["error"]
        if isinstance(exc, DaytonaRunCancelled):
            await _emit_stream_event(
                websocket,
                StreamEvent(
                    kind="cancelled",
                    text=str(exc),
                    payload={
                        "runtime_mode": "daytona_pilot",
                        "history_turns": 1,
                    },
                    timestamp=datetime.now(timezone.utc),
                ),
            )
            return

        await _emit_stream_event(
            websocket,
            StreamEvent(
                kind="error",
                text=str(exc),
                payload={
                    "runtime_mode": "daytona_pilot",
                    "history_turns": 1,
                },
                timestamp=datetime.now(timezone.utc),
            ),
        )
        return

    result = result_box["result"]
    root = result.nodes.get(result.root_id)
    runtime_payload = {
        "depth": root.depth if root is not None else 0,
        "max_depth": result.budget.max_depth,
        "execution_profile": "DAYTONA_PILOT",
        "sandbox_active": root is not None and root.sandbox_id is not None,
        "effective_max_iters": result.budget.max_iterations,
        "runtime_mode": "daytona_pilot",
        "execution_mode": "daytona_pilot",
        "sandbox_id": root.sandbox_id if root is not None else None,
    }
    final_text = _render_final_text(
        result.final_artifact.value if result.final_artifact else ""
    )
    await _emit_stream_event(
        websocket,
        StreamEvent(
            kind="final",
            text=final_text,
            payload={
                "history_turns": 1,
                "runtime_mode": "daytona_pilot",
                "repo_url": result.repo,
                "repo_ref": result.ref,
                "final_artifact": (
                    result.final_artifact.to_dict()
                    if result.final_artifact is not None
                    else None
                ),
                "summary": result.summary.to_dict(),
                "result_path": result.result_path,
                "runtime": runtime_payload,
            },
            timestamp=datetime.now(timezone.utc),
        ),
    )
