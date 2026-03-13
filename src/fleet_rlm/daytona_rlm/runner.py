"""Thin host adapter for the self-orchestrated Daytona-backed RLM pilot."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fleet_rlm.models import StreamEvent

from .config import resolve_daytona_lm_runtime_config
from .results import persist_result
from .sandbox import DaytonaSandboxRuntime
from .protocol import RunEventFrame, RunStartRequest
from .types import DaytonaRunResult, FinalArtifact, RolloutBudget

_ROOT_MIN_CHARS = 80
_ROOT_MIN_WORDS = 12
_WORD_RE = re.compile(r"\b\w+\b")
_WHITESPACE_RE = re.compile(r"\s+")
_PATH_LINE_RE = re.compile(r"^(?:/|\.{1,2}/|[A-Za-z]:\\|[A-Za-z0-9._-]+/).*$")
_GREP_LINE_RE = re.compile(r"^[^:\n]+:\d+(?::\d+)?(?:\s*(?:-|\|)\s*.*|: .*)?$")


def _collapse_plain_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


class DaytonaRLMRunner:
    """Launch Daytona rollouts while keeping orchestration inside the sandbox."""

    def __init__(
        self,
        *,
        lm: Any | None = None,
        runtime: DaytonaSandboxRuntime | None = None,
        budget: RolloutBudget | None = None,
        output_dir: Path | str = "results/daytona-rlm",
        event_callback: Callable[[StreamEvent], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        del lm
        self.runtime = runtime or DaytonaSandboxRuntime()
        self.budget = budget or RolloutBudget()
        self.output_dir = Path(output_dir)
        self.event_callback = event_callback
        self.cancel_check = cancel_check
        self.run_id = str(uuid.uuid4())

    def run(
        self,
        *,
        repo: str | None,
        task: str,
        ref: str | None = None,
        context_paths: list[str] | None = None,
    ) -> DaytonaRunResult:
        lm_config = resolve_daytona_lm_runtime_config()
        create_workspace_session = getattr(
            self.runtime, "create_workspace_session", None
        )
        if callable(create_workspace_session):
            session = create_workspace_session(
                repo_url=repo,
                ref=ref,
                context_paths=context_paths,
            )
        else:
            if context_paths:
                raise RuntimeError(
                    "Configured Daytona runtime does not support context_paths."
                )
            session = self.runtime.create_repo_session(repo_url=repo, ref=ref)
        context_sources = [item.to_dict() for item in session.context_sources]
        request = RunStartRequest(
            request_id=uuid.uuid4().hex,
            payload={
                "run_id": self.run_id,
                "node_id": uuid.uuid4().hex,
                "parent_id": None,
                "depth": 0,
                "repo": repo or "",
                "ref": ref,
                "context_sources": context_sources,
                "task": task,
                "workspace_path": session.workspace_path,
                "repo_path": session.repo_path,
                "sandbox_id": session.sandbox_id,
                "budget": asdict(self.budget),
                "lm_config": lm_config.to_dict(),
                "remaining_sandboxes": self.budget.max_sandboxes,
                "deadline_epoch_s": time.time() + self.budget.global_timeout,
            },
        )
        try:
            result = session.run_rollout(
                request=request,
                timeout=self.budget.global_timeout + 120.0,
                event_handler=self._handle_runtime_event,
                cancel_check=self.cancel_check,
            )
        finally:
            session.close_controller()
            session.delete()

        self._apply_root_synthesis_guard(result)
        persist_result(result, output_dir=self.output_dir)
        return result

    def _handle_runtime_event(self, frame: RunEventFrame) -> None:
        if self.event_callback is None:
            return
        kind = frame.kind
        if kind not in {
            "status",
            "tool_call",
            "tool_result",
            "final",
            "error",
            "cancelled",
            "warning",
            "reasoning_step",
        }:
            kind = "status"
        self.event_callback(
            StreamEvent(
                kind=kind,  # type: ignore[arg-type]
                text=frame.text,
                payload=frame.payload or {},
            )
        )

    def _apply_root_synthesis_guard(self, result: DaytonaRunResult) -> None:
        if result.summary.termination_reason == "cancelled":
            return
        if result.final_artifact is None:
            return
        if self._root_finalization_candidate(result.final_artifact) is not None:
            return
        invalid_message = (
            "Root Daytona run returned raw intermediate data instead of a "
            "human-readable synthesized answer."
        )
        result.final_artifact = FinalArtifact(
            kind="error",
            value=invalid_message,
            finalization_mode="error",
        )
        root = result.nodes.get(result.root_id)
        if root is not None:
            root.final_artifact = result.final_artifact
            root.status = "error"
            root.error = invalid_message
        result.summary.termination_reason = "invalid_final_artifact"
        result.summary.error = invalid_message

    def _root_finalization_candidate(self, artifact: FinalArtifact) -> str | None:
        value = artifact.value
        candidate = self._extract_synthesized_text(value)
        if candidate is None:
            return None
        normalized = _collapse_plain_text(candidate)
        if not normalized:
            return None
        if len(normalized) < _ROOT_MIN_CHARS:
            return None
        if len(_WORD_RE.findall(normalized)) < _ROOT_MIN_WORDS:
            return None
        if self._looks_like_unsynthesized_root_payload(value=value, raw_text=candidate):
            return None
        return candidate

    @staticmethod
    def _extract_synthesized_text(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("summary", "final_markdown"):
                candidate = value.get(key)
                if candidate is None:
                    continue
                text = str(candidate)
                if text.strip():
                    return text
        return None

    def _looks_like_unsynthesized_root_payload(
        self, *, value: Any, raw_text: str
    ) -> bool:
        if isinstance(value, (list, tuple)):
            return True
        if isinstance(value, dict) and self._extract_synthesized_text(value) is None:
            return True
        stripped = raw_text.strip()
        if (
            not stripped
            or stripped.startswith("```")
            or stripped.startswith("{")
            or stripped.startswith("[")
        ):
            return True
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if len(lines) >= 2:
            if all(_PATH_LINE_RE.match(line) for line in lines):
                return True
            grep_like_count = sum(1 for line in lines if _GREP_LINE_RE.match(line))
            if grep_like_count >= max(2, len(lines) - 1):
                return True
        return False


def run_daytona_rlm_pilot(
    *,
    repo: str | None,
    task: str,
    ref: str | None = None,
    context_paths: list[str] | None = None,
    budget: RolloutBudget | None = None,
    output_dir: Path | str = "results/daytona-rlm",
    runtime: Any | None = None,
    lm: Any | None = None,
) -> DaytonaRunResult:
    """Run the experimental Daytona-backed RLM pilot."""

    runner = DaytonaRLMRunner(
        lm=lm,
        runtime=runtime,
        budget=budget,
        output_dir=output_dir,
    )
    return runner.run(repo=repo, task=task, ref=ref, context_paths=context_paths)
