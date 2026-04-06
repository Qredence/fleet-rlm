"""Batch orchestration tools for parallel semantic analysis and recursive batching.

Contains ``parallel_semantic_map`` (sandbox-based batch analysis over document
chunks) and ``rlm_query_batched`` (Daytona-specific parallel recursive queries).
These tools are conditionally registered based on the active interpreter backend.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.recursive_runtime import spawn_delegate_sub_agent_async
from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .sandbox_common import _aexecute_submit_ctx, _SandboxToolContext
from .shared import (
    build_trajectory_payload,
    chunk_text,
    chunk_to_text,
    resolve_document,
)

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


def build_batch_tools(
    agent: RLMReActChatAgent,
) -> tuple[list[Any], list[Any]]:
    """Build batch orchestration tools bound to *agent*.

    Returns ``(prepend, append)`` — tools to insert before and after the
    core delegate tool list respectively.  ``parallel_semantic_map`` is
    returned in *prepend* when Daytona recursive batching is unavailable;
    ``rlm_query_batched`` is returned in *append* when it **is** available.
    """
    sandbox_ctx = _SandboxToolContext(agent=agent)

    try:
        from fleet_rlm.integrations.daytona.interpreter import (
            DaytonaInterpreter,
        )
    except Exception:  # pragma: no cover - defensive import guard
        DaytonaInterpreter = None  # type: ignore[assignment]

    # -- Daytona helpers -------------------------------------------------------

    def _supports_daytona_recursive_batching() -> bool:
        return bool(
            DaytonaInterpreter is not None
            and isinstance(agent.interpreter, DaytonaInterpreter)
        )

    def _effective_recursive_batch_concurrency(task_count: int) -> int:
        configured = getattr(agent, "daytona_batch_concurrency", None)
        try:
            value = int(configured) if configured is not None else 0
        except (TypeError, ValueError):
            value = 0

        if value <= 0:
            delegate_limit = max(
                1, int(getattr(agent, "delegate_max_calls_per_turn", 1))
            )
            value = min(delegate_limit, 4)

        delegate_limit = max(1, int(getattr(agent, "delegate_max_calls_per_turn", 1)))
        return max(1, min(value, delegate_limit, max(1, int(task_count))))

    def _normalize_batched_task(
        task: Any, index: int
    ) -> tuple[str, str] | dict[str, Any]:
        if not isinstance(task, dict):
            return {
                "index": index,
                "callback_name": "rlm_query_batched",
                "status": "error",
                "error": "Each task must be an object with query and optional context.",
            }

        query = str(task.get("query", "") or "").strip()
        context = str(task.get("context", "") or "")
        if not query:
            return {
                "index": index,
                "callback_name": "rlm_query_batched",
                "status": "error",
                "error": "Task query cannot be empty.",
            }
        return query, context

    # -- Tool callables --------------------------------------------------------

    async def parallel_semantic_map(
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        buffer_name: str = "findings",
    ) -> dict[str, Any]:
        """Run parallel semantic analysis over chunks via llm_query_batched."""
        text = resolve_document(agent, "active")
        chunks = chunk_text(
            text, chunk_strategy, size=80_000, overlap=1_000, pattern=""
        )
        chunk_texts = [chunk_to_text(chunk) for chunk in chunks][:max_chunks]
        prompts = [
            "Query: "
            f"{query}\nChunk index: {idx}\nReturn concise findings as plain text.\n\n"
            f"{chunk_text[:6000]}"
            for idx, chunk_text in enumerate(chunk_texts)
        ]

        code = """
clear_buffer(buffer_name)
responses = llm_query_batched(prompts)
for idx, response in enumerate(responses):
    add_buffer(buffer_name, {"chunk_index": idx, "response": response})

SUBMIT(
    status="ok",
    strategy=chunk_strategy,
    chunk_count=len(prompts),
    findings_count=len(responses),
    buffer_name=buffer_name,
)
"""
        return await _aexecute_submit_ctx(
            sandbox_ctx,
            code,
            variables={
                "prompts": prompts,
                "buffer_name": buffer_name,
                "chunk_strategy": chunk_strategy,
            },
        )

    async def rlm_query_batched(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """Run multiple bounded recursive sub-agent queries in parallel."""
        if not _supports_daytona_recursive_batching():
            return {
                "status": "error",
                "error": "rlm_query_batched is only available in the Daytona runtime.",
            }

        if not isinstance(tasks, list) or not tasks:
            return {
                "status": "error",
                "error": "tasks must be a non-empty list of {query, context} objects.",
            }

        results: list[dict[str, Any] | None] = [None] * len(tasks)
        semaphore = asyncio.Semaphore(
            _effective_recursive_batch_concurrency(len(tasks))
        )

        async def _run_one(index: int, raw_task: Any) -> None:
            normalized = _normalize_batched_task(raw_task, index)
            if isinstance(normalized, dict):
                results[index] = normalized
                return

            query, context = normalized
            async with semaphore:
                result = await spawn_delegate_sub_agent_async(
                    agent,
                    prompt=query,
                    context=context,
                    stream_event_callback=getattr(agent, "_live_event_callback", None),
                )

            if result.get("status") == "error":
                results[index] = {
                    "index": index,
                    "query": query,
                    "context": context,
                    "callback_name": "rlm_query_batched",
                    "status": "error",
                    "error": str(
                        result.get("error", "Unknown recursive batching error.")
                    ),
                }
                return

            results[index] = {
                "index": index,
                "query": query,
                "context": context,
                "callback_name": "rlm_query_batched",
                "status": "ok",
                "answer": result.get("answer") or result.get("assistant_response", ""),
                "sub_agent_history": result.get("sub_agent_history", 0),
                "depth": result.get("depth", getattr(agent, "_current_depth", 0) + 1),
                **build_trajectory_payload(result, include_trajectory=True),
            }

        await asyncio.gather(
            *(_run_one(index, raw_task) for index, raw_task in enumerate(tasks))
        )

        ordered_results = [item for item in results if isinstance(item, dict)]
        success_count = sum(1 for item in ordered_results if item.get("status") == "ok")

        return {
            "status": "ok",
            "callback_name": "rlm_query_batched",
            "task_count": len(tasks),
            "success_count": success_count,
            "error_count": len(tasks) - success_count,
            "batch_concurrency": _effective_recursive_batch_concurrency(len(tasks)),
            "results": ordered_results,
        }

    # -- Build and return tool lists -------------------------------------------
    from dspy import Tool

    def _build_batch_tool(name: str, desc: str, func: Any) -> Any:
        return Tool(_sync_compatible_tool_callable(func), name=name, desc=desc)

    prepend: list[Any] = []
    append: list[Any] = []

    if not _supports_daytona_recursive_batching():
        prepend.append(
            _build_batch_tool(
                "parallel_semantic_map",
                "Run batched semantic analysis over document chunks and store findings in a buffer",
                parallel_semantic_map,
            )
        )

    if _supports_daytona_recursive_batching():
        append.append(
            _build_batch_tool(
                "rlm_query_batched",
                "Run multiple bounded recursive sub-agent queries in parallel and return ordered per-task results",
                rlm_query_batched,
            )
        )

    return prepend, append
