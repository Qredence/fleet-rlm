from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import dspy
import modal

from .config import configure_planner_from_env
from .interpreter import ModalInterpreter
from .signatures import (
    ExtractAPIEndpoints,
    ExtractArchitecture,
    ExtractWithCustomTool,
    FindErrorPatterns,
)
from .tools import regex_extract


DEFAULT_DOCS_PATH = Path("dspy-doc/dspy-doc.txt")


def _require_planner_ready(env_file: Path | None = None) -> None:
    ready = configure_planner_from_env(env_file=env_file)
    if not ready and dspy.settings.lm is None:
        raise RuntimeError(
            "Planner LM not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY (or DSPY_LM_API_KEY)."
        )


def _read_docs(path: Path | str) -> str:
    docs_path = Path(path)
    if not docs_path.exists():
        raise FileNotFoundError(f"Docs path does not exist: {docs_path}")
    return docs_path.read_text()


def _interpreter(*, timeout: int = 600, secret_name: str = "LITELLM") -> ModalInterpreter:
    return ModalInterpreter(timeout=timeout, secret_name=secret_name)


def run_basic(
    *,
    question: str,
    max_iterations: int = 15,
    max_llm_calls: int = 30,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    env_file: Path | None = None,
) -> dict[str, Any]:
    _require_planner_ready(env_file)

    interpreter = _interpreter(timeout=timeout, secret_name=secret_name)
    rlm = dspy.RLM(
        signature="question -> answer",
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(question=question)
        return {
            "answer": result.answer,
            "trajectory_steps": len(getattr(result, "trajectory", [])),
        }
    finally:
        interpreter.shutdown()


def run_architecture(
    *,
    docs_path: Path | str = DEFAULT_DOCS_PATH,
    query: str,
    max_iterations: int = 25,
    max_llm_calls: int = 50,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    env_file: Path | None = None,
) -> dict[str, Any]:
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(timeout=timeout, secret_name=secret_name)
    rlm = dspy.RLM(
        signature=ExtractArchitecture,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(docs=docs, query=query)
        return {
            "modules": result.modules,
            "optimizers": result.optimizers,
            "design_principles": result.design_principles,
            "doc_chars": len(docs),
            "doc_lines": len(docs.splitlines()),
        }
    finally:
        interpreter.shutdown()


def run_api_endpoints(
    *,
    docs_path: Path | str = DEFAULT_DOCS_PATH,
    max_iterations: int = 20,
    max_llm_calls: int = 30,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    env_file: Path | None = None,
) -> dict[str, Any]:
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(timeout=timeout, secret_name=secret_name)
    rlm = dspy.RLM(
        signature=ExtractAPIEndpoints,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(docs=docs)
        return {
            "api_endpoints": result.api_endpoints,
            "count": len(result.api_endpoints),
        }
    finally:
        interpreter.shutdown()


def run_error_patterns(
    *,
    docs_path: Path | str = DEFAULT_DOCS_PATH,
    max_iterations: int = 30,
    max_llm_calls: int = 40,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    env_file: Path | None = None,
) -> dict[str, Any]:
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(timeout=timeout, secret_name=secret_name)
    rlm = dspy.RLM(
        signature=FindErrorPatterns,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(docs=docs)
        return {
            "error_categories": result.error_categories,
            "total_errors_found": result.total_errors_found,
        }
    finally:
        interpreter.shutdown()


def run_trajectory(
    *,
    docs_path: Path | str = DEFAULT_DOCS_PATH,
    chars: int = 3000,
    max_iterations: int = 10,
    max_llm_calls: int = 10,
    verbose: bool = False,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    env_file: Path | None = None,
) -> dict[str, Any]:
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(timeout=timeout, secret_name=secret_name)
    rlm = dspy.RLM(
        signature="text -> summary",
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        sample = docs[:chars]
        result = rlm(text=sample)
        trajectory = []
        for idx, step in enumerate(getattr(result, "trajectory", []), start=1):
            trajectory.append(
                {
                    "step": idx,
                    "reasoning": str(step.get("reasoning", "N/A"))[:100],
                    "code": str(step.get("code", ""))[:60],
                }
            )
        return {
            "summary": result.summary,
            "trajectory_steps": len(trajectory),
            "trajectory": trajectory,
        }
    finally:
        interpreter.shutdown()


def run_custom_tool(
    *,
    docs_path: Path | str = DEFAULT_DOCS_PATH,
    chars: int = 10000,
    max_iterations: int = 15,
    max_llm_calls: int = 20,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    env_file: Path | None = None,
) -> dict[str, Any]:
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(timeout=timeout, secret_name=secret_name)
    rlm = dspy.RLM(
        signature=ExtractWithCustomTool,
        interpreter=interpreter,
        tools=[regex_extract],
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(docs=docs[:chars])
        return {
            "headers": result.headers,
            "code_blocks": result.code_blocks,
            "structure_summary": result.structure_summary,
            "headers_count": len(result.headers),
            "code_blocks_count": len(result.code_blocks),
        }
    finally:
        interpreter.shutdown()


def check_secret_presence(*, secret_name: str = "LITELLM") -> dict[str, bool]:
    app = modal.App.lookup("dspy-rlm-secret-check", create_if_missing=True)
    sb = modal.Sandbox.create(app=app, secrets=[modal.Secret.from_name(secret_name)])
    try:
        code = r'''
import json, os
keys = [
  "DSPY_LM_MODEL",
  "DSPY_LM_API_BASE",
  "DSPY_LLM_API_KEY",
  "DSPY_LM_MAX_TOKENS",
]
print(json.dumps({k: bool(os.environ.get(k)) for k in keys}))
'''
        proc = sb.exec("python", "-c", code, timeout=60)
        proc.wait()
        return json.loads(proc.stdout.read().strip())
    finally:
        sb.terminate()


def check_secret_key(*, secret_name: str = "LITELLM", key: str = "DSPY_LLM_API_KEY") -> dict[str, Any]:
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
