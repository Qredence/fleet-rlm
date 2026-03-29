"""High-level runner functions for RLM workflows.

This module provides convenient functions for running RLM-based tasks
using DSPy signatures and provider-backed interpreters. Each runner
function handles the complete lifecycle: configuration, execution, and
cleanup.

Runner categories:
    - Long-context analysis/summarization: run_long_context
    - Diagnostics: check_secret_presence, check_secret_key
    - Interactive ReAct chat: build_react_chat_agent, run_react_chat_once

All runners automatically:
    1. Configure the DSPy planner from environment
    2. Create and start the selected interpreter runtime
    3. Execute the RLM with appropriate signature
    4. Return structured results
    5. Clean up resources (interpreter shutdown)
"""

from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
from typing import Any, Literal

import dspy

from fleet_rlm.runtime.agent.signatures import (
    AnalyzeLongDocument,
    SummarizeLongDocument,
)
from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.execution.interpreter import ModalInterpreter
from fleet_rlm.integrations.observability.mlflow_runtime import (
    MlflowTraceRequestContext,
    merge_trace_result_metadata,
    mlflow_request_context,
    new_client_request_id,
)
from .runtime_factory import (
    _build_react_agent_from_options,
    _ReActAgentOptions,
    _require_planner_ready,
    build_chat_agent_for_runtime_mode,
    build_daytona_workbench_chat_agent,
    build_react_chat_agent,
)

__all__ = [
    "build_chat_agent_for_runtime_mode",
    "build_daytona_workbench_chat_agent",
    "build_react_chat_agent",
    "check_secret_key",
    "check_secret_presence",
    "run_long_context",
    "run_react_chat_once",
    "arun_react_chat_once",
]


def _local_runner_user_id() -> str:
    candidate = (os.getenv("USER") or os.getenv("USERNAME") or "").strip()
    return candidate or "local-user"


def _runner_trace_context(
    *,
    entrypoint: str,
    request_preview: str,
    metadata: dict[str, str] | None = None,
) -> MlflowTraceRequestContext:
    return MlflowTraceRequestContext(
        client_request_id=new_client_request_id(prefix=entrypoint),
        session_id=f"runner:{entrypoint}",
        user_id=_local_runner_user_id(),
        app_env=(os.getenv("APP_ENV") or "local").strip().lower(),
        request_preview=request_preview,
        metadata={
            "fleet_rlm.entrypoint": entrypoint,
            **(metadata or {}),
        },
    )


def _rlm_trajectory_payload(result: Any, *, include_trajectory: bool) -> dict[str, Any]:
    """Build a normalized trajectory payload from a DSPy RLM result."""
    if not include_trajectory:
        return {}

    trajectory = list(getattr(result, "trajectory", []) or [])
    payload: dict[str, Any] = {
        "trajectory_steps": len(trajectory),
        "trajectory": trajectory,
    }
    final_reasoning = getattr(result, "final_reasoning", None)
    if final_reasoning:
        payload["final_reasoning"] = final_reasoning
    return payload


def run_react_chat_once(
    *,
    message: str,
    docs_path: Path | str | None = None,
    react_max_iters: int = 15,
    deep_react_max_iters: int = 35,
    enable_adaptive_iters: bool = True,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    max_depth: int = 2,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    verbose: bool = False,
    include_trajectory: bool = True,
    env_file: Path | None = None,
    interpreter_async_execute: bool = True,
    guardrail_mode: Literal["off", "warn", "strict"] = "off",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
    delegate_lm: Any | None = None,
    delegate_max_calls_per_turn: int = 8,
    delegate_result_truncation_chars: int = 8000,
) -> dict[str, Any]:
    """Run a single prompt through the interactive ReAct chat agent."""
    options = _ReActAgentOptions(
        react_max_iters=react_max_iters,
        deep_react_max_iters=deep_react_max_iters,
        enable_adaptive_iters=enable_adaptive_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        max_depth=max_depth,
        timeout=timeout,
        secret_name=secret_name,
        volume_name=volume_name,
        verbose=verbose,
        interpreter_async_execute=interpreter_async_execute,
        guardrail_mode=guardrail_mode,
        max_output_chars=max_output_chars,
        min_substantive_chars=min_substantive_chars,
        delegate_lm=delegate_lm,
        delegate_max_calls_per_turn=delegate_max_calls_per_turn,
        delegate_result_truncation_chars=delegate_result_truncation_chars,
    )
    with (
        _build_react_agent_from_options(
            options=options,
            docs_path=docs_path,
            env_file=env_file,
            planner_lm=None,
        ) as agent,
        mlflow_request_context(
            _runner_trace_context(
                entrypoint="run-react-chat-once",
                request_preview=message,
            )
        ),
    ):
        result = agent.chat_turn(message)
        if not include_trajectory:
            result.pop("trajectory", None)
        return merge_trace_result_metadata(
            result,
            response_preview=result.get("assistant_response"),
        )


async def arun_react_chat_once(
    *,
    message: str,
    docs_path: Path | str | None = None,
    react_max_iters: int = 15,
    deep_react_max_iters: int = 35,
    enable_adaptive_iters: bool = True,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    max_depth: int = 2,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    verbose: bool = False,
    include_trajectory: bool = True,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
    interpreter_async_execute: bool = True,
    guardrail_mode: Literal["off", "warn", "strict"] = "off",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
    delegate_lm: Any | None = None,
    delegate_max_calls_per_turn: int = 8,
    delegate_result_truncation_chars: int = 8000,
) -> dict[str, Any]:
    """Async version of ``run_react_chat_once`` using ``achat_turn``."""
    options = _ReActAgentOptions(
        react_max_iters=react_max_iters,
        deep_react_max_iters=deep_react_max_iters,
        enable_adaptive_iters=enable_adaptive_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        max_depth=max_depth,
        timeout=timeout,
        secret_name=secret_name,
        volume_name=volume_name,
        verbose=verbose,
        interpreter_async_execute=interpreter_async_execute,
        guardrail_mode=guardrail_mode,
        max_output_chars=max_output_chars,
        min_substantive_chars=min_substantive_chars,
        delegate_lm=delegate_lm,
        delegate_max_calls_per_turn=delegate_max_calls_per_turn,
        delegate_result_truncation_chars=delegate_result_truncation_chars,
    )
    agent = _build_react_agent_from_options(
        options=options,
        docs_path=docs_path,
        env_file=env_file,
        planner_lm=planner_lm,
    )
    try:
        with build_dspy_context(lm=planner_lm) if planner_lm else nullcontext():
            with agent:
                with mlflow_request_context(
                    _runner_trace_context(
                        entrypoint="arun-react-chat-once",
                        request_preview=message,
                    )
                ):
                    result = await agent.achat_turn(message)
                    if not include_trajectory:
                        result.pop("trajectory", None)
                    return merge_trace_result_metadata(
                        result,
                        response_preview=result.get("assistant_response"),
                    )
    except Exception:
        agent.shutdown()
        raise


def run_long_context(
    *,
    docs_path: Path | str,
    query: str,
    mode: str = "analyze",
    max_iterations: int = 30,
    max_llm_calls: int = 50,
    verbose: bool = True,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    include_trajectory: bool = True,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run a long-context analysis or summarization task.

    Loads a document into the sandbox and uses injected helpers
    (``peek``, ``grep``, ``chunk_by_size``, ``chunk_by_headers``,
    buffers, volume persistence) to let the RLM explore it
    programmatically.

    Args:
        docs_path: Path to the document file.
        query: The analysis query or focus topic.
        mode: ``"analyze"`` (default) or ``"summarize"``.
        max_iterations: Maximum RLM iterations (default: 30).
        max_llm_calls: Maximum LLM calls (default: 50).
        verbose: Enable verbose output (default: True).
        timeout: Sandbox timeout in seconds (default: 900).
        secret_name: Modal secret name (default: "LITELLM").
        volume_name: Optional Modal volume name for persistence.
        include_trajectory: Include RLM trajectory metadata in output.
        env_file: Optional path to .env file.

    Returns:
        Dictionary with results specific to the chosen mode.

    Raises:
        ValueError: If *mode* is not ``"analyze"`` or ``"summarize"``.
    """
    if mode not in ("analyze", "summarize"):
        raise ValueError(f"mode must be 'analyze' or 'summarize', got {mode!r}")

    docs_path = Path(docs_path)
    if not docs_path.exists():
        raise FileNotFoundError(f"Docs path does not exist: {docs_path}")
    docs = docs_path.read_text()
    _require_planner_ready(env_file)

    sig = AnalyzeLongDocument if mode == "analyze" else SummarizeLongDocument

    with (
        ModalInterpreter(
            timeout=timeout, secret_name=secret_name, volume_name=volume_name
        ) as interpreter,
        mlflow_request_context(
            _runner_trace_context(
                entrypoint="run-long-context",
                request_preview=query,
                metadata={
                    "fleet_rlm.mode": mode,
                    "fleet_rlm.docs_path": str(docs_path),
                },
            )
        ),
    ):
        rlm = dspy.RLM(
            signature=sig,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

        if mode == "analyze":
            result = rlm(document=docs, query=query)
            response: dict[str, Any] = {
                "findings": result.findings,
                "answer": result.answer,
                "sections_examined": result.sections_examined,
                "doc_chars": len(docs),
            }
            response_preview = str(getattr(result, "answer", "") or "")
        else:
            result = rlm(document=docs, focus=query)
            response = {
                "summary": result.summary,
                "key_points": result.key_points,
                "coverage_pct": result.coverage_pct,
                "doc_chars": len(docs),
            }
            response_preview = str(getattr(result, "summary", "") or "")

        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return merge_trace_result_metadata(
            response,
            response_preview=response_preview,
        )


def check_secret_presence(*, secret_name: str = "LITELLM") -> dict[str, bool]:
    """Check which DSPy environment variables are present in a Modal secret.

    Creates a temporary sandbox with the specified secret and checks
    for the presence of DSPy-related environment variables.

    Args:
        secret_name: Name of the Modal secret to check (default: "LITELLM").

    Returns:
        Dictionary mapping environment variable names to boolean presence.
    """
    import modal

    app = modal.App.lookup("dspy-rlm-secret-check", create_if_missing=True)
    sb = modal.Sandbox.create(app=app, secrets=[modal.Secret.from_name(secret_name)])
    try:
        code = (
            "import json, os\n"
            "keys = ['DSPY_LM_MODEL','DSPY_LM_API_BASE','DSPY_LLM_API_KEY','DSPY_LM_MAX_TOKENS']\n"
            "print(json.dumps({k: bool(os.environ.get(k)) for k in keys}))\n"
        )
        proc = sb.exec("python", "-c", code, timeout=60)
        proc.wait()
        return json.loads(proc.stdout.read().strip())
    finally:
        sb.terminate()


def check_secret_key(
    *, secret_name: str = "LITELLM", key: str = "DSPY_LLM_API_KEY"
) -> dict[str, Any]:
    """Check a specific environment variable in a Modal secret.

    Args:
        secret_name: Name of the Modal secret to check (default: "LITELLM").
        key: Environment variable name to check (default: "DSPY_LLM_API_KEY").

    Returns:
        Dictionary with ``present`` (bool) and ``length`` (int) keys.
    """
    import modal

    app = modal.App.lookup("dspy-rlm-secret-check", create_if_missing=True)
    sb = modal.Sandbox.create(app=app, secrets=[modal.Secret.from_name(secret_name)])
    try:
        code = (
            "import json, os\n"
            f"val=os.environ.get({key!r}, '')\n"
            "print(json.dumps({'present': bool(val), 'length': len(val)}))\n"
        )
        proc = sb.exec("python", "-c", code, timeout=60)
        proc.wait()
        return json.loads(proc.stdout.read().strip())
    finally:
        sb.terminate()
