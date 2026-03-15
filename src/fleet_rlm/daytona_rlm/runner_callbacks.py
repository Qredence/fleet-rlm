"""Host-callback dispatch helpers for Daytona host-loop runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .protocol import HostCallbackRequest, HostCallbackResponse
from .runner_events import DaytonaRuntimeEventEmitter
from .types import (
    AgentNode,
    ChildLink,
    ChildTaskResult,
    DaytonaRunCancelled,
    RecursiveTaskSpec,
)


class DaytonaRunnerProtocol(Protocol):
    """Subset of DaytonaRLMRunner used by callback dispatch helpers."""

    _host_callbacks: dict[str, Callable[..., Any]]

    def run_semantic_task(self, *, task_spec: RecursiveTaskSpec) -> str: ...

    def run_semantic_tasks_batched(
        self, *, task_specs: list[RecursiveTaskSpec]
    ) -> list[str]: ...

    def run_child_task(
        self,
        *,
        parent_id: str,
        depth: int,
        task_spec: RecursiveTaskSpec,
        parent_task: str | None = None,
    ) -> ChildTaskResult: ...

    def _spawn_child_tasks_batched(
        self,
        *,
        parent_id: str,
        depth: int,
        parent_task: str | None,
        task_specs: list[RecursiveTaskSpec],
    ) -> list[ChildTaskResult]: ...


class DaytonaHostCallbackDispatcher:
    """Normalize and execute structured host callbacks for one node run."""

    def __init__(
        self,
        *,
        runner: DaytonaRunnerProtocol,
        task: str,
        event_emitter: DaytonaRuntimeEventEmitter,
        active_iteration_getter: Callable[[], int | None],
        merge_child_result: Callable[[AgentNode, ChildTaskResult, str], None],
    ) -> None:
        self._runner = runner
        self._task = task
        self._event_emitter = event_emitter
        self._active_iteration_getter = active_iteration_getter
        self._merge_child_result = merge_child_result

    def handle(
        self, *, node: AgentNode, request: HostCallbackRequest
    ) -> HostCallbackResponse:
        handler = self._registry().get(request.name)
        if handler is None:
            return HostCallbackResponse(
                callback_id=request.callback_id,
                ok=False,
                error=f"Unsupported host callback: {request.name}",
            )
        try:
            value = handler(payload=request.payload, node=node)
            return HostCallbackResponse(
                callback_id=request.callback_id,
                ok=True,
                value=value,
            )
        except DaytonaRunCancelled:
            raise
        except Exception as exc:
            return HostCallbackResponse(
                callback_id=request.callback_id,
                ok=False,
                error=str(exc),
            )

    def _registry(self) -> dict[str, Callable[..., Any]]:
        registry: dict[str, Callable[..., Any]] = {
            "llm_query": self._callback_llm_query,
            "llm_query_batched": self._callback_llm_query_batched,
            "rlm_query": self._callback_rlm_query,
            "rlm_query_batched": self._callback_rlm_query_batched,
        }
        registry.update(self._runner._host_callbacks)
        return registry

    @staticmethod
    def _task_spec_from_payload(raw_task: Any) -> RecursiveTaskSpec:
        return RecursiveTaskSpec.from_raw(raw_task)

    @staticmethod
    def _preview(value: str, *, limit: int) -> str:
        collapsed = " ".join(str(value).split()).strip()
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[:limit].rstrip()

    def _callback_llm_query(self, *, payload: dict[str, Any], node: AgentNode) -> str:
        task_spec = self._task_spec_from_payload(payload.get("task"))
        tool_input = {"task": task_spec.to_dict()}
        self._event_emitter.emit_tool_call(node, "llm_query", tool_input)
        result = self._runner.run_semantic_task(task_spec=task_spec)
        node.child_links.append(
            ChildLink(
                child_id=None,
                callback_name="llm_query",
                iteration=self._active_iteration_getter(),
                task=task_spec,
                result_preview=self._preview(result, limit=280),
                status="completed",
            )
        )
        self._event_emitter.emit_tool_result(
            node,
            "llm_query",
            {"result_preview": self._preview(result, limit=280)},
            tool_input=tool_input,
        )
        return result

    def _callback_llm_query_batched(
        self, *, payload: dict[str, Any], node: AgentNode
    ) -> list[str]:
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            raise ValueError("llm_query_batched expects a list payload.")

        task_specs = [self._task_spec_from_payload(item) for item in raw_tasks]
        tool_input = {"tasks": [item.to_dict() for item in task_specs]}
        self._event_emitter.emit_tool_call(node, "llm_query_batched", tool_input)
        if not task_specs:
            self._event_emitter.emit_tool_result(
                node,
                "llm_query_batched",
                {"count": 0},
                tool_input=tool_input,
            )
            return []

        values = self._runner.run_semantic_tasks_batched(task_specs=task_specs)
        for task_spec, value in zip(task_specs, values, strict=False):
            node.child_links.append(
                ChildLink(
                    child_id=None,
                    callback_name="llm_query_batched",
                    iteration=self._active_iteration_getter(),
                    task=task_spec,
                    result_preview=self._preview(value, limit=280),
                    status="completed",
                )
            )
        self._event_emitter.emit_tool_result(
            node,
            "llm_query_batched",
            {
                "count": len(values),
                "result_previews": [
                    self._preview(value, limit=180) for value in values
                ],
            },
            tool_input=tool_input,
        )
        return values

    def _callback_rlm_query(self, *, payload: dict[str, Any], node: AgentNode) -> str:
        task_spec = self._task_spec_from_payload(payload.get("task"))
        tool_input = {"task": task_spec.to_dict()}
        self._event_emitter.emit_tool_call(node, "rlm_query", tool_input)
        child_result = self._runner.run_child_task(
            parent_id=node.node_id,
            depth=node.depth,
            task_spec=task_spec,
            parent_task=self._task,
        )
        self._merge_child_result(node, child_result, "rlm_query")
        self._event_emitter.emit_tool_result(
            node,
            "rlm_query",
            {
                "child_id": child_result.child_id,
                "result_preview": child_result.result_preview,
                "status": child_result.status,
            },
            tool_input=tool_input,
        )
        return child_result.text

    def _callback_rlm_query_batched(
        self, *, payload: dict[str, Any], node: AgentNode
    ) -> list[str]:
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list):
            raise ValueError("rlm_query_batched expects a list payload.")

        task_specs = [self._task_spec_from_payload(item) for item in raw_tasks]
        tool_input = {"tasks": [item.to_dict() for item in task_specs]}
        self._event_emitter.emit_tool_call(node, "rlm_query_batched", tool_input)
        if not task_specs:
            self._event_emitter.emit_tool_result(
                node,
                "rlm_query_batched",
                {"count": 0},
                tool_input=tool_input,
            )
            return []

        values: list[str] = []
        child_results = self._runner._spawn_child_tasks_batched(
            parent_id=node.node_id,
            depth=node.depth,
            parent_task=self._task,
            task_specs=task_specs,
        )
        for child_result in child_results:
            self._merge_child_result(node, child_result, "rlm_query_batched")
            values.append(child_result.text)
        self._event_emitter.emit_tool_result(
            node,
            "rlm_query_batched",
            {
                "count": len(values),
                "result_previews": [
                    child_result.result_preview for child_result in child_results
                ],
            },
            tool_input=tool_input,
        )
        return values
