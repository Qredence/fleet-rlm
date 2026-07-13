"""Observable ``dspy.RLM`` with bounded, sanitized per-iteration details."""

from __future__ import annotations

import ast
import asyncio
import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from typing import Any
from uuid import uuid4

from dspy.predict.rlm import RLM, _strip_code_fences
from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.rlm.errors import RLMBudgetError
from fleet_rlm.rlm.sanitize import sanitize_public_text, sanitize_public_value


class RLMDetailKind(StrEnum):
    STEP_STARTED = "step.started"
    REASONING = "reasoning"
    CODE = "code"
    OUTPUT = "output"
    STEP_FINISHED = "step.finished"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"


@dataclass(frozen=True, slots=True)
class RLMDetail:
    kind: RLMDetailKind
    payload: dict[str, Any]


DetailObserver = Callable[[RLMDetail], None]


def _safe_value(value: Any, *, max_len: int = 2_000) -> Any:
    return sanitize_public_value(value, max_len=max_len)


def _argument(args: tuple[Any, ...], kwargs: dict[str, Any], name: str, index: int) -> Any:
    return kwargs.get(name, args[index] if len(args) > index else None)


def _public_tool_input(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Project protected host arguments without publishing bodies or prompts."""
    if name == "read_attachment":
        return {"attachment_id": _safe_value(_argument(args, kwargs, "attachment_id", 0))}
    if name == "create_artifact":
        content = _argument(args, kwargs, "content", 1)
        return {
            "kind": _safe_value(_argument(args, kwargs, "kind", 0)),
            "title": _safe_value(_argument(args, kwargs, "title", 2)),
            "content_chars": len(str(content or "")),
        }
    if name == "load_skill":
        return {"skill_id": _safe_value(_argument(args, kwargs, "skill_id", 0))}
    if name == "read_skill_resource":
        return {
            "skill_id": _safe_value(_argument(args, kwargs, "skill_id", 0)),
            "resource_id": _safe_value(_argument(args, kwargs, "resource_id", 1)),
        }
    if name == "llm_query":
        prompt = _argument(args, kwargs, "prompt", 0)
        return {"prompt_chars": len(str(prompt or ""))}
    if name == "llm_query_batched":
        prompts = _argument(args, kwargs, "prompts", 0)
        values = list(prompts) if isinstance(prompts, (list, tuple)) else []
        return {"prompt_count": len(values), "prompt_chars": sum(len(str(value)) for value in values)}
    return {"args": _safe_value(args), "kwargs": _safe_value(kwargs)}


def _public_tool_output(name: str, result: Any) -> Any:
    """Project protected host results; private bodies stay inside the RLM."""
    if not isinstance(result, dict):
        return _safe_value(result)
    if name == "read_attachment":
        allowed = ("ok", "attachment_id", "filename", "content_type", "encoding")
    elif name == "create_artifact":
        allowed = ("ok", "kind", "title", "byte_size")
    elif name == "load_skill":
        allowed = ("ok", "skill_id", "name", "version", "trust")
    elif name == "read_skill_resource":
        allowed = ("ok", "skill_id", "resource_id", "byte_size")
    else:
        return _safe_value(result)
    return {key: _safe_value(result[key]) for key in allowed if key in result}


class ObservableRLM(RLM):  # ty: ignore[invalid-base] - DSPy @experimental obscures the class type
    """DSPy RLM that publishes user-safe actions without exposing provider internals."""

    def __init__(
        self,
        *args: Any,
        observer: DetailObserver | None = None,
        detail_max_chars: int = 10_000,
        max_tool_calls: int = 32,
        max_sub_lm_concurrency: int = 8,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._fleet_observer = observer
        self._fleet_detail_max_chars = max(256, int(detail_max_chars))
        self._fleet_private_tokens: set[str] = set()
        self._fleet_protected_data_accessed = False
        self._fleet_max_tool_calls = max(1, int(max_tool_calls))
        self._fleet_max_sub_lm_concurrency = max(1, int(max_sub_lm_concurrency))
        self._fleet_tool_calls = 0
        self._fleet_sub_lm_calls = 0
        self._fleet_tool_budget_exhausted = False
        self._fleet_tool_lock = threading.Lock()

    @property
    def tool_budget_exhausted(self) -> bool:
        return self._fleet_tool_budget_exhausted

    @property
    def tool_calls_used(self) -> int:
        return self._fleet_tool_calls

    @property
    def sub_lm_calls_used(self) -> int:
        return self._fleet_sub_lm_calls

    def _claim_tool_call(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        with self._fleet_tool_lock:
            if self._fleet_tool_calls >= self._fleet_max_tool_calls:
                self._fleet_tool_budget_exhausted = True
                raise RLMBudgetError("Turn tool-call budget exhausted")
            self._fleet_tool_calls += 1
            if name == "llm_query":
                self._fleet_sub_lm_calls += 1
            elif name == "llm_query_batched":
                prompts = _argument(args, kwargs, "prompts", 0)
                self._fleet_sub_lm_calls += len(prompts) if isinstance(prompts, (list, tuple)) else 0

    def _make_llm_tools(self, max_workers: int = 8) -> dict[str, Callable[..., Any]]:
        return super()._make_llm_tools(max_workers=min(max_workers, self._fleet_max_sub_lm_concurrency))

    def _remember_private_values(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any = None,
    ) -> None:
        values: list[Any] = []
        if name == "create_artifact":
            self._fleet_protected_data_accessed = True
            values.append(_argument(args, kwargs, "content", 1))
        elif name == "llm_query":
            self._fleet_protected_data_accessed = True
            values.append(_argument(args, kwargs, "prompt", 0))
        elif name == "llm_query_batched":
            self._fleet_protected_data_accessed = True
            values.extend(list(_argument(args, kwargs, "prompts", 0) or ()))
        if isinstance(result, dict):
            protected = {
                "artifact_candidate_id",
                "checksum_sha256",
                "content",
                "content_base64",
                "instructions",
                "body",
            }
            protected_values = [value for key, value in result.items() if str(key) in protected]
            if protected_values:
                self._fleet_protected_data_accessed = True
            values.extend(protected_values)
        for value in values:
            text = str(value or "")
            if len(text) >= 6:
                self._fleet_private_tokens.add(text)

    def _sanitize_detail_text(self, value: Any) -> str:
        text: str = sanitize_public_text(str(value), max_len=self._fleet_detail_max_chars)
        private_values: list[str] = sorted(
            self._fleet_private_tokens,
            key=lambda item: len(item),
            reverse=True,
        )
        for private in private_values:
            text = text.replace(private, "[private]")
        return text

    def _sanitize_code(self, code: str) -> str:
        """Keep generated Python visible while replacing protected call bodies."""

        class _ProtectedCallRedactor(ast.NodeTransformer):
            def visit_Constant(self, node: ast.Constant) -> ast.AST:  # noqa: N802 - ast API
                if isinstance(node.value, str):
                    return ast.copy_location(ast.Constant(f"[string:{len(node.value)}]"), node)
                return node

            def visit_Call(self, node: ast.Call) -> ast.AST:  # noqa: N802 - ast API
                self.generic_visit(node)
                name = node.func.id if isinstance(node.func, ast.Name) else None
                if name == "create_artifact":
                    if len(node.args) > 1:
                        node.args[1] = ast.Constant("[redacted-artifact-content]")
                    for keyword in node.keywords:
                        if keyword.arg == "content":
                            keyword.value = ast.Constant("[redacted-artifact-content]")
                elif name == "llm_query":
                    if node.args:
                        node.args[0] = ast.Constant("[redacted-subquery-prompt]")
                    for keyword in node.keywords:
                        if keyword.arg == "prompt":
                            keyword.value = ast.Constant("[redacted-subquery-prompt]")
                elif name == "llm_query_batched":
                    replacement = ast.List(elts=[ast.Constant("[redacted-subquery-prompts]")], ctx=ast.Load())
                    if node.args:
                        node.args[0] = replacement
                    for keyword in node.keywords:
                        if keyword.arg == "prompts":
                            keyword.value = replacement
                return node

        try:
            tree = _ProtectedCallRedactor().visit(ast.parse(code))
            ast.fix_missing_locations(tree)
            projected = ast.unparse(tree)
        except (SyntaxError, ValueError):
            return "Generated code omitted because it could not be safely projected"
        return self._sanitize_detail_text(projected)

    def _public_reasoning(self, reasoning: Any) -> str:
        if self._fleet_protected_data_accessed:
            return "RLM reasoning omitted after protected data access"
        return self._sanitize_detail_text(reasoning)

    def _public_interpreter_output(self, result: Any) -> str:
        if isinstance(result, FinalOutput):
            return "FINAL submitted"
        if self._fleet_protected_data_accessed:
            return "Interpreter output omitted after protected data access"
        return self._sanitize_detail_text(result or "")

    def _observe(self, kind: RLMDetailKind, payload: dict[str, Any]) -> None:
        if self._fleet_observer is None:
            return
        self._fleet_observer(RLMDetail(kind=kind, payload=payload))

    def _prepare_execution_tools(self) -> dict[str, Callable[..., Any]]:
        tools = super()._prepare_execution_tools()
        return {name: self._instrument_tool(name, function) for name, function in tools.items()}

    def _instrument_tool(self, name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            call_id = str(uuid4())
            self._remember_private_values(name, args, kwargs)
            self._observe(
                RLMDetailKind.TOOL_STARTED,
                {
                    "tool_call_id": call_id,
                    "tool_name": name,
                    "input": _public_tool_input(name, args, kwargs),
                },
            )
            try:
                self._claim_tool_call(name, args, kwargs)
                result = function(*args, **kwargs)
                if inspect.isawaitable(result):
                    raise TypeError("async host tools are not supported inside the synchronous interpreter bridge")
            except Exception as exc:
                self._observe(
                    RLMDetailKind.TOOL_FAILED,
                    {
                        "tool_call_id": call_id,
                        "tool_name": name,
                        "error": (
                            "Protected tool failed"
                            if self._fleet_protected_data_accessed
                            else sanitize_public_text(str(exc), max_len=240)
                        ),
                    },
                )
                raise
            self._remember_private_values(name, args, kwargs, result)
            self._observe(
                RLMDetailKind.TOOL_COMPLETED,
                {
                    "tool_call_id": call_id,
                    "tool_name": name,
                    "output": _public_tool_output(name, result),
                },
            )
            return result

        return wrapped

    async def _aexecute_iteration(
        self,
        repl: Any,
        variables: list[Any],
        history: Any,
        iteration: int,
        input_args: dict[str, Any],
        output_field_names: list[str],
    ) -> Any:
        """Mirror installed DSPy iteration behavior while publishing safe details."""
        step = iteration + 1
        self._observe(RLMDetailKind.STEP_STARTED, {"step": step, "max_steps": self.max_iterations})
        variables_info = [variable.format() for variable in variables]
        prediction = await self.generate_action.acall(
            variables_info=variables_info,
            repl_history=history,
            iteration=f"{step}/{self.max_iterations}",
        )
        reasoning = self._public_reasoning(prediction.reasoning)
        self._observe(RLMDetailKind.REASONING, {"step": step, "text": reasoning})
        try:
            code = _strip_code_fences(prediction.code)
        except SyntaxError as exc:
            code = str(prediction.code)
            syntax_error: SyntaxError | None = exc
        else:
            syntax_error = None
        self._observe(
            RLMDetailKind.CODE,
            {"step": step, "code": self._sanitize_code(code)},
        )
        if syntax_error is not None:
            result: Any = f"[Error] {syntax_error}"
        else:
            result = await asyncio.to_thread(self._execute_code, repl, code, input_args)

        output = self._public_interpreter_output(result)
        self._observe(RLMDetailKind.OUTPUT, {"step": step, "output": output})
        processed = self._process_execution_result(
            prediction,
            code,
            result,
            history,
            output_field_names,
        )
        self._observe(RLMDetailKind.STEP_FINISHED, {"step": step})
        return processed
