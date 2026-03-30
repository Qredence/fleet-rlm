"""Thin Daytona compatibility wrapper over the shared ReAct + RLM agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent
from fleet_rlm.runtime.models import StreamEvent

from .interpreter import DaytonaInterpreter
from .runtime import DaytonaSandboxRuntime
from .state import (
    dedupe_paths,
    normalize_history_turn,
)


class DaytonaWorkbenchChatAgent(RLMReActChatAgent):
    """Compatibility wrapper that uses Daytona as the interpreter backend only."""

    def __init__(
        self,
        *,
        runtime: DaytonaSandboxRuntime | None = None,
        history_max_turns: int | None = None,
        planner_lm: Any | None = None,
        delegate_lm: Any | None = None,
        delete_session_on_shutdown: bool = True,
        react_max_iters: int = 15,
        deep_react_max_iters: int = 35,
        enable_adaptive_iters: bool = True,
        rlm_max_iterations: int = 30,
        rlm_max_llm_calls: int = 50,
        max_depth: int = 2,
        timeout: int = 900,
        verbose: bool = False,
        guardrail_mode: str = "off",
        max_output_chars: int = 10000,
        min_substantive_chars: int = 20,
        delegate_max_calls_per_turn: int = 8,
        delegate_result_truncation_chars: int = 8000,
        interpreter_async_execute: bool = True,
    ) -> None:
        _ = planner_lm
        self.runtime = runtime or DaytonaSandboxRuntime()
        self.loaded_document_paths: list[str] = []
        self.daytona_batch_concurrency: int | None = None

        interpreter = DaytonaInterpreter(
            runtime=self.runtime,
            timeout=timeout,
            delete_session_on_shutdown=delete_session_on_shutdown,
            max_llm_calls=rlm_max_llm_calls,
            async_execute=interpreter_async_execute,
        )

        super().__init__(
            react_max_iters=react_max_iters,
            deep_react_max_iters=deep_react_max_iters,
            enable_adaptive_iters=enable_adaptive_iters,
            rlm_max_iterations=rlm_max_iterations,
            rlm_max_llm_calls=rlm_max_llm_calls,
            timeout=timeout,
            verbose=verbose,
            history_max_turns=history_max_turns,
            interpreter=interpreter,
            max_depth=max_depth,
            guardrail_mode=guardrail_mode,  # type: ignore[arg-type]
            max_output_chars=max_output_chars,
            min_substantive_chars=min_substantive_chars,
            delegate_lm=delegate_lm,
            delegate_max_calls_per_turn=delegate_max_calls_per_turn,
            delegate_result_truncation_chars=delegate_result_truncation_chars,
        )

    def _build_task_prompt(self, message: str) -> str:
        return str(message or "").strip()

    def load_document(self, path: str, alias: str = "active") -> dict[str, Any]:
        result = self._get_tool("load_document")(path, alias=alias)
        normalized = str(path or "").strip()
        if normalized:
            self.loaded_document_paths = dedupe_paths(
                [*self.loaded_document_paths, normalized]
            )
        return result

    def export_session_state(self) -> dict[str, Any]:
        payload = super().export_session_state()
        history = []
        raw_history = payload.get("history", [])
        if not isinstance(raw_history, list):
            raw_history = []
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            turn = normalize_history_turn(item)
            if turn is not None:
                history.append(turn)
        payload["history"] = history
        daytona = payload.get("daytona", {})
        if not isinstance(daytona, dict):
            daytona = {}
        daytona["loaded_document_paths"] = list(self.loaded_document_paths)
        payload["daytona"] = daytona
        return payload

    def _normalized_import_state(self, state: dict[str, Any]) -> dict[str, Any]:
        raw_daytona = state.get("daytona", {})
        daytona_state = raw_daytona if isinstance(raw_daytona, dict) else {}
        self.loaded_document_paths = dedupe_paths(
            [str(item) for item in daytona_state.get("loaded_document_paths", []) or []]
        )
        history = state.get("history", [])
        if isinstance(history, list):
            normalized_history = []
            for item in history:
                if not isinstance(item, dict):
                    continue
                turn = normalize_history_turn(item)
                if turn is not None:
                    normalized_history.append(turn)
            state = dict(state)
            state["history"] = normalized_history
        return state

    def import_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return super().import_session_state(self._normalized_import_state(state))

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return await super().aimport_session_state(self._normalized_import_state(state))

    def _effective_context_paths(
        self, *, docs_path: str | None, context_paths: list[str] | None
    ) -> list[str]:
        return dedupe_paths(
            [
                *self.loaded_document_paths,
                *(context_paths or []),
                *([str(docs_path)] if docs_path else []),
            ]
        )

    def _configure_daytona_workspace(
        self,
        *,
        docs_path: str | None,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
    ) -> None:
        interpreter = cast(DaytonaInterpreter, self.interpreter)
        interpreter.configure_workspace(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=self._effective_context_paths(
                docs_path=docs_path,
                context_paths=context_paths,
            ),
            volume_name=volume_name,
        )

    async def _aconfigure_daytona_workspace(
        self,
        *,
        docs_path: str | None,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
    ) -> None:
        interpreter = cast(DaytonaInterpreter, self.interpreter)
        await interpreter.aconfigure_workspace(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=self._effective_context_paths(
                docs_path=docs_path,
                context_paths=context_paths,
            ),
            volume_name=volume_name,
        )

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
        volume_name: str | None = None,
    ):
        if batch_concurrency is not None:
            self.daytona_batch_concurrency = (
                max(1, int(batch_concurrency))
                if isinstance(batch_concurrency, int) and batch_concurrency > 0
                else None
            )
        interpreter = cast(DaytonaInterpreter, self.interpreter)
        effective_repo_url = repo_url if repo_url is not None else interpreter.repo_url
        effective_repo_ref = repo_ref if repo_ref is not None else interpreter.repo_ref
        effective_context_inputs = (
            list(context_paths)
            if context_paths is not None
            else list(interpreter.context_paths)
        )
        effective_volume_name = (
            volume_name if volume_name is not None else interpreter.volume_name
        )
        await self._aconfigure_daytona_workspace(
            docs_path=docs_path,
            repo_url=effective_repo_url,
            repo_ref=effective_repo_ref,
            context_paths=effective_context_inputs,
            volume_name=effective_volume_name,
        )
        if (
            interpreter._session is not None
            or interpreter._persisted_sandbox_id is not None
        ):
            await interpreter.aget_session()
        effective_context_paths = self._effective_context_paths(
            docs_path=docs_path,
            context_paths=effective_context_inputs,
        )

        yield StreamEvent(
            kind="status",
            text="Bootstrapping Daytona RLM session",
            payload={
                "runtime_mode": "daytona_pilot",
                "repo_url": effective_repo_url,
                "repo_ref": effective_repo_ref,
                "context_paths": effective_context_paths,
                "runtime": {
                    "runtime_mode": "daytona_pilot",
                    "execution_mode": self.execution_mode,
                    "depth": self.current_depth,
                    "max_depth": self._max_depth,
                    "execution_profile": str(
                        getattr(
                            self.interpreter.default_execution_profile,
                            "value",
                            self.interpreter.default_execution_profile,
                        )
                    ),
                    "sandbox_active": False,
                    "sandbox_id": None,
                    "effective_max_iters": max(
                        self.react_max_iters, self.rlm_max_iterations
                    ),
                    "volume_name": self.interpreter.volume_name,
                },
            },
        )

        async for event in super().aiter_chat_turn_stream(
            message=message,
            trace=trace,
            cancel_check=cancel_check,
            docs_path=docs_path,
        ):
            payload = dict(event.payload or {})
            runtime_payload = dict(payload.get("runtime", {}) or {})
            runtime_payload.setdefault("runtime_mode", "daytona_pilot")
            runtime_payload.setdefault("volume_name", self.interpreter.volume_name)
            payload["runtime"] = runtime_payload
            payload.setdefault("runtime_mode", "daytona_pilot")
            yield StreamEvent(
                kind=event.kind,
                text=event.text,
                payload=payload,
                timestamp=event.timestamp,
                flush_tokens=event.flush_tokens,
            )
