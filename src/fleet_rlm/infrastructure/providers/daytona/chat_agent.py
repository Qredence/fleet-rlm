"""DSPy-native chat wrapper for Daytona host-loop workbench sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import dspy

from fleet_rlm.core.models import StreamEvent
from fleet_rlm.infrastructure.providers.daytona.runner import DaytonaRLMRunner

from .sandbox import DaytonaSandboxRuntime, DaytonaSandboxSession
from .types import ContextSource, DaytonaRunCancelled, DaytonaRunResult, RolloutBudget


def _render_final_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("final_markdown", "summary", "text", "content", "message"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        nested_value = value.get("value")
        if nested_value is not value:
            nested_text = _render_final_text(nested_value)
            if nested_text:
                return nested_text
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _render_cancelled_text(result: DaytonaRunResult) -> str:
    warnings = list(result.summary.warnings or [])
    base = result.summary.error or "Daytona run cancelled."
    if warnings:
        return f"{base}\n\nWarnings:\n- " + "\n- ".join(warnings)
    return str(base)


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in paths:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _history_messages(history: Any) -> list[dict[str, str]]:
    messages = getattr(history, "messages", [])
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    return []


def _normalize_history_turn(raw: dict[str, Any]) -> dict[str, str] | None:
    user_request = str(raw.get("user_request", "") or "").strip()
    assistant_response = _render_final_text(raw.get("assistant_response", "")).strip()
    if not user_request and not assistant_response:
        return None
    return {
        "user_request": user_request,
        "assistant_response": assistant_response,
    }


def _normalized_history_messages(history: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in _history_messages(history):
        turn = _normalize_history_turn(item)
        if turn is not None:
            normalized.append(turn)
    return normalized


def _normalized_context_sources(raw: Any) -> list[ContextSource]:
    if not isinstance(raw, list):
        return []
    normalized: list[ContextSource] = []
    for item in raw:
        try:
            normalized.append(ContextSource.from_raw(item))
        except Exception:
            continue
    return normalized


class DaytonaWorkbenchChatAgent(dspy.Module):
    """Stateful Daytona chat runtime that streams workbench and chat events."""

    def __init__(
        self,
        *,
        runtime: DaytonaSandboxRuntime | None = None,
        budget: RolloutBudget | None = None,
        output_dir: Path | str = "results/daytona-rlm",
        history_max_turns: int | None = None,
        planner_lm: Any | None = None,
        delegate_lm: Any | None = None,
        delete_session_on_shutdown: bool = True,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.default_budget = budget or RolloutBudget()
        self.output_dir = Path(output_dir)
        self.history_max_turns = history_max_turns
        self.planner_lm = planner_lm
        self.delegate_lm = delegate_lm
        self.delete_session_on_shutdown = delete_session_on_shutdown
        self.history = dspy.History(messages=[])
        self.execution_mode: str = "auto"
        self.repo_url: str | None = None
        self.repo_ref: str | None = None
        self.context_paths: list[str] = []
        self.loaded_document_paths: list[str] = []
        self._session: DaytonaSandboxSession | None = None
        self._session_source_key: (
            tuple[str | None, str | None, tuple[str, ...]] | None
        ) = None
        self._persisted_sandbox_id: str | None = None
        self._persisted_workspace_path: str | None = None
        self._persisted_context_sources: list[ContextSource] = []

    def __enter__(self) -> DaytonaWorkbenchChatAgent:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        self.shutdown()
        return False

    async def __aenter__(self) -> DaytonaWorkbenchChatAgent:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        self.shutdown()
        return False

    def shutdown(self) -> None:
        self._detach_session(delete=self.delete_session_on_shutdown)

    def _persist_session_snapshot(
        self, session: DaytonaSandboxSession | None = None
    ) -> None:
        active_session = session or self._session
        if active_session is None:
            return
        self._persisted_sandbox_id = active_session.sandbox_id
        self._persisted_workspace_path = active_session.workspace_path
        self._persisted_context_sources = list(active_session.context_sources)

    def _detach_session(self, *, delete: bool) -> None:
        if self._session is None:
            if delete:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []
            return

        active_session = self._session
        self._persist_session_snapshot(active_session)
        try:
            if delete:
                active_session.delete()
            else:
                active_session.close_driver()
        finally:
            if delete:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []
            self._session = None
            self._session_source_key = None

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        _ = clear_sandbox_buffers
        self.history = dspy.History(messages=[])
        self.loaded_document_paths = []
        self._detach_session(delete=True)
        return {"status": "ok", "history_turns": 0, "buffers_cleared": True}

    def history_turns(self) -> int:
        return len(_history_messages(self.history))

    def set_execution_mode(self, execution_mode: str) -> None:
        normalized = (
            execution_mode
            if execution_mode in {"auto", "rlm_only", "tools_only"}
            else "auto"
        )
        self.execution_mode = normalized

    def load_document(self, path: str, alias: str = "active") -> None:
        _ = alias
        normalized = str(path or "").strip()
        if not normalized:
            return
        self.loaded_document_paths = _dedupe_paths(
            [*self.loaded_document_paths, normalized]
        )

    def export_session_state(self) -> dict[str, Any]:
        document_map = {path: {"path": path} for path in self.loaded_document_paths}
        self._persist_session_snapshot()
        context_sources = (
            list(self._session.context_sources)
            if self._session is not None
            else list(self._persisted_context_sources)
        )
        return {
            "history": list(_normalized_history_messages(self.history)),
            "documents": document_map,
            "daytona": {
                "repo_url": self.repo_url,
                "repo_ref": self.repo_ref,
                "context_paths": list(self.context_paths),
                "loaded_document_paths": list(self.loaded_document_paths),
                "execution_mode": self.execution_mode,
                "sandbox_id": (
                    self._session.sandbox_id
                    if self._session is not None
                    else self._persisted_sandbox_id
                ),
                "workspace_path": (
                    self._session.workspace_path
                    if self._session is not None
                    else self._persisted_workspace_path
                ),
                "context_sources": [item.to_dict() for item in context_sources],
            },
        }

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        self._detach_session(delete=False)
        history = state.get("history", [])
        normalized_history: list[dict[str, str]] = []
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, dict):
                    continue
                turn = _normalize_history_turn(item)
                if turn is not None:
                    normalized_history.append(turn)
        self.history = dspy.History(messages=normalized_history)

        raw_daytona = state.get("daytona", {})
        daytona_state = raw_daytona if isinstance(raw_daytona, dict) else {}
        self.repo_url = cast(str | None, daytona_state.get("repo_url"))
        self.repo_ref = cast(str | None, daytona_state.get("repo_ref"))
        self.context_paths = _dedupe_paths(
            [str(item) for item in daytona_state.get("context_paths", []) or []]
        )
        documents = state.get("documents", {})
        document_paths = list(documents.keys()) if isinstance(documents, dict) else []
        loaded_document_paths = [
            str(item)
            for item in daytona_state.get("loaded_document_paths", []) or document_paths
        ]
        self.loaded_document_paths = _dedupe_paths(loaded_document_paths)
        self.execution_mode = str(daytona_state.get("execution_mode", "auto") or "auto")
        self._persisted_sandbox_id = cast(str | None, daytona_state.get("sandbox_id"))
        self._persisted_workspace_path = cast(
            str | None, daytona_state.get("workspace_path")
        )
        self._persisted_context_sources = _normalized_context_sources(
            daytona_state.get("context_sources", [])
        )
        self._session_source_key = None
        return {
            "status": "ok",
            "history_turns": self.history_turns(),
            "documents": len(self.loaded_document_paths),
        }

    def _ensure_runtime(self) -> DaytonaSandboxRuntime:
        if self.runtime is None:
            self.runtime = DaytonaSandboxRuntime()
        return self.runtime

    def _effective_context_paths(
        self, extra_context_paths: list[str] | None = None
    ) -> list[str]:
        return _dedupe_paths(
            [
                *self.context_paths,
                *self.loaded_document_paths,
                *(extra_context_paths or []),
            ]
        )

    def _ensure_session(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str],
    ) -> DaytonaSandboxSession:
        source_key = (repo_url, repo_ref, tuple(context_paths))
        if self._session is not None and self._session_source_key == source_key:
            return self._session

        if self._session is not None:
            self._detach_session(delete=True)

        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = list(context_paths)
        runtime = self._ensure_runtime()
        if (
            self._persisted_sandbox_id
            and self._persisted_workspace_path
            and source_key == (self.repo_url, self.repo_ref, tuple(context_paths))
        ):
            try:
                self._session = runtime.resume_workspace_session(
                    sandbox_id=self._persisted_sandbox_id,
                    repo_url=repo_url,
                    ref=repo_ref,
                    workspace_path=self._persisted_workspace_path,
                    context_sources=self._persisted_context_sources,
                )
                self._session_source_key = source_key
                self._persist_session_snapshot()
                return self._session
            except Exception:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []
        self._session = runtime.create_workspace_session(
            repo_url=repo_url,
            ref=repo_ref,
            context_paths=context_paths,
        )
        self._session_source_key = source_key
        self._persist_session_snapshot()
        return self._session

    def _build_budget(
        self,
        *,
        batch_concurrency: int | None,
    ) -> RolloutBudget:
        return RolloutBudget(
            max_sandboxes=self.default_budget.max_sandboxes,
            max_depth=self.default_budget.max_depth,
            max_iterations=self.default_budget.max_iterations,
            global_timeout=self.default_budget.global_timeout,
            result_truncation_limit=self.default_budget.result_truncation_limit,
            batch_concurrency=(
                batch_concurrency
                if batch_concurrency is not None
                else self.default_budget.batch_concurrency
            ),
        )

    def _build_task_prompt(self, message: str) -> str:
        return str(message or "").strip()

    def _append_history(self, *, user_request: str, assistant_response: str) -> None:
        messages = list(_normalized_history_messages(self.history))
        turn = _normalize_history_turn(
            {
                "user_request": user_request,
                "assistant_response": assistant_response,
            }
        )
        if turn is not None:
            messages.append(turn)
        if self.history_max_turns is not None and self.history_max_turns > 0:
            messages = messages[-self.history_max_turns :]
        self.history = dspy.History(messages=messages)

    def _run_turn_blocking(
        self,
        *,
        message: str,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str],
        budget: RolloutBudget,
        event_callback,
        cancel_check,
    ) -> DaytonaRunResult:
        session = self._ensure_session(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
        )
        runner = DaytonaRLMRunner(
            lm=self.planner_lm,
            delegate_lm=self.delegate_lm,
            runtime=self._ensure_runtime(),
            budget=budget,
            output_dir=self.output_dir,
            event_callback=event_callback,
            cancel_check=cancel_check,
        )
        return runner.run(
            repo=repo_url,
            ref=repo_ref,
            context_paths=context_paths,
            task=self._build_task_prompt(message),
            conversation_history=_normalized_history_messages(self.history),
            session=session,
        )

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check=None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
    ):
        _ = trace
        if docs_path:
            self.load_document(str(docs_path))

        effective_repo_url = repo_url if repo_url is not None else self.repo_url
        effective_repo_ref = (
            repo_ref
            if repo_ref is not None
            else (self.repo_ref if repo_url is None else None)
        )
        effective_context_paths = self._effective_context_paths(context_paths)
        budget = self._build_budget(batch_concurrency=batch_concurrency)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        result_box: dict[str, DaytonaRunResult] = {}
        error_box: dict[str, BaseException] = {}

        def enqueue_event(event: StreamEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        if self.execution_mode == "tools_only":
            yield StreamEvent(
                kind="status",
                text="Daytona workbench does not support tools-only mode; continuing with host-loop RLM reasoning.",
                payload={
                    "runtime_mode": "daytona_pilot",
                    "warning": True,
                    "execution_mode_requested": "tools_only",
                    "execution_mode_effective": "auto",
                },
            )

        yield StreamEvent(
            kind="status",
            text="Bootstrapping Daytona workbench runtime",
            payload={
                "runtime": {
                    "runtime_mode": "daytona_pilot",
                    "daytona_mode": "host_loop_rlm",
                    "run_id": None,
                    "depth": 0,
                    "max_depth": budget.max_depth,
                    "effective_max_iters": budget.max_iterations,
                    "sandbox_active": False,
                },
                "runtime_mode": "daytona_pilot",
                "daytona_mode": "host_loop_rlm",
                "repo_url": effective_repo_url,
                "repo_ref": effective_repo_ref,
                "context_paths": effective_context_paths,
            },
        )

        async def run_in_thread() -> None:
            try:
                result = await asyncio.to_thread(
                    self._run_turn_blocking,
                    message=message,
                    repo_url=effective_repo_url,
                    repo_ref=effective_repo_ref,
                    context_paths=effective_context_paths,
                    budget=budget,
                    event_callback=enqueue_event,
                    cancel_check=cancel_check,
                )
                result_box["result"] = result
            except Exception as exc:  # noqa: BLE001 - surfaced by tests
                error_box["error"] = exc

        task = asyncio.create_task(run_in_thread())

        while True:
            if task.done() and queue.empty():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            yield event

        await task

        if "error" in error_box:
            exc = error_box["error"]
            if isinstance(exc, DaytonaRunCancelled):
                cancelled_text = str(exc)
                self._append_history(
                    user_request=message,
                    assistant_response=cancelled_text,
                )
                yield StreamEvent(
                    kind="cancelled",
                    text=cancelled_text,
                    payload={
                        "runtime_mode": "daytona_pilot",
                        "history_turns": self.history_turns(),
                    },
                )
                return

            error_text = str(exc)
            yield StreamEvent(
                kind="error",
                text=error_text,
                payload={
                    "runtime_mode": "daytona_pilot",
                    "history_turns": self.history_turns(),
                },
            )
            return

        result = result_box["result"]
        root = result.nodes.get(result.root_id)
        public_result = result.to_public_dict()
        runtime_payload = {
            "depth": root.depth if root is not None else 0,
            "max_depth": result.budget.max_depth,
            "execution_profile": "DAYTONA_PILOT_HOST_LOOP",
            "sandbox_active": root is not None and root.sandbox_id is not None,
            "effective_max_iters": result.budget.max_iterations,
            "runtime_mode": "daytona_pilot",
            "daytona_mode": "host_loop_rlm",
            "sandbox_id": root.sandbox_id if root is not None else None,
            "run_id": result.run_id,
            "phase_timings_ms": dict(result.summary.phase_timings_ms),
        }

        terminal_kind = (
            "cancelled" if result.summary.termination_reason == "cancelled" else "final"
        )
        terminal_text = (
            _render_cancelled_text(result)
            if terminal_kind == "cancelled"
            else _render_final_text(
                result.final_artifact.value if result.final_artifact else ""
            )
        )
        self._append_history(user_request=message, assistant_response=terminal_text)

        yield StreamEvent(
            kind=terminal_kind,
            text=terminal_text,
            payload={
                "history_turns": self.history_turns(),
                "runtime_mode": "daytona_pilot",
                "repo_url": result.repo or None,
                "repo_ref": result.ref,
                "context_sources": [item.to_dict() for item in result.context_sources],
                "prompts": public_result.get("prompts", []),
                "iterations": public_result.get("iterations", []),
                "callbacks": public_result.get("callbacks", []),
                "sources": public_result.get("sources", []),
                "attachments": public_result.get("attachments", []),
                "final_artifact": (
                    result.final_artifact.to_dict()
                    if result.final_artifact is not None
                    else None
                ),
                "summary": result.summary.to_dict(),
                "result_path": result.result_path,
                "run_result": public_result,
                "runtime": runtime_payload,
            },
        )
