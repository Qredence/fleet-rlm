"""DaytonaInterpreter: DSPy's CodeInterpreter over one namespace of a Session sandbox."""

from __future__ import annotations

import contextvars
import keyword
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar

import dspy
import mlflow
from mlflow.entities import SpanType

from fleet_rlm.dspy_support import decision_evidence, without_mlflow_dspy_callback
from fleet_rlm.engine.signatures import item_decision
from fleet_rlm.observability import activity_preview, activity_text
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, _NATIVE_CHILDREN, DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError

_LOGGER = logging.getLogger(__name__)


class DaytonaInterpreter:
    """DSPy CodeInterpreter backed by a namespace in a Session-isolated sandbox."""

    def __init__(
        self,
        manager: DaytonaSandboxManager,
        tools: dict[str, Callable[..., Any]] | None = None,
        owns_manager: bool = False,
    ):
        self.manager = manager
        self.owns_manager = owns_manager
        self.tools = dict(tools or {})
        reserved = {
            "__builtins__",
            "llm_query",
            "llm_query_batched",
            "jev_decide_batched",
            "SUBMIT",
            "list_skills",
            "use_skill",
            "read_skill_reference",
            "run_skill_script",
            "save_skill",
        }
        if any(
            not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name in reserved
            for name in self.tools
        ):
            raise ValueError("Bridged tool names must be unreserved Python identifiers")
        self.ns_id = f"ns_{uuid.uuid4().hex[:8]}"
        self._context = contextvars.copy_context()
        self._started = False
        self._closed = False
        self._host_calls = 0
        self._host_call_lock = threading.Lock()

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Interpreter has already been shut down")
        self.manager.ensure_sandbox()
        if not self._started:
            self.manager._register_interpreter(self)
            self._started = True

    def _invoke_tool(self, tool_name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        """The host invocation, not interpreter shutdown, owns child success."""
        children: list[Any] = []
        token = _NATIVE_CHILDREN.set(children)
        try:
            value = self.tools[tool_name](*args, **kwargs)
            if hasattr(self.manager, "check_turn"):
                self.manager.check_turn()
            for child in children:
                child.collect_child_artifacts()
            return value
        finally:
            _NATIVE_CHILDREN.reset(token)
            for child in children:
                child.shutdown()

    def answer_host_call(self, call: dict[str, Any]) -> dict[str, Any]:
        """Run one bridged call in this interpreter's DSPy context; the reply the sandbox is waiting for."""
        return self._context.copy().run(self._handle_host_call, call)

    def _handle_host_call(self, call: dict[str, Any]) -> dict[str, Any]:
        with self._host_call_lock:
            self._host_calls += 1
        call_id = call.get("id")
        tool_name = call.get("tool")
        args = call.get("args", [])
        kwargs = call.get("kwargs", {})
        started = time.monotonic()
        details = {"call_id": call_id, "tool": tool_name, "source": "sandbox_bridge", "namespace": self.ns_id}
        # Facade calls and semantic LM queries return predictor internals, not tool evidence.
        predictor_call = str(tool_name).startswith("__dspy_") or tool_name in {"llm_query", "llm_query_batched"}
        if hasattr(self.manager, "emit"):
            self.manager.emit(
                "tool_start",
                **details,
                inputs="[predictor payload omitted]"
                if predictor_call
                else activity_preview({"args": args, "kwargs": kwargs}),
            )
        active_token = _ACTIVE_MANAGER.set(self.manager)
        try:
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise ValueError("Invalid host tool arguments")
            builtin = self._BUILTINS.get(str(tool_name))
            if builtin is not None:
                value = builtin(self, args, kwargs)
            elif tool_name in self.tools:
                value = self._invoke_tool(tool_name, args, kwargs)
            else:
                raise KeyError(f"Unknown bridged tool: {tool_name}")
            if hasattr(self.manager, "emit"):
                self.manager.emit(
                    "tool_end",
                    **details,
                    ok=True,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    output="[predictor payload omitted]" if predictor_call else activity_preview(value),
                )
            return {"value": value}
        except Exception as exc:
            if hasattr(self.manager, "emit"):
                self.manager.emit(
                    "tool_end",
                    **details,
                    ok=False,
                    error_type=type(exc).__name__,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            return {"error": str(exc)[:2000]}
        finally:
            _ACTIVE_MANAGER.reset(active_token)

    def _llm_query(self, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if len(args) != 1 or not isinstance(args[0], str) or len(args[0]) > 100_000:
            raise ValueError("llm_query requires one prompt of at most 100000 characters")
        if "llm_query" in self.tools:  # a Flex-registered override
            return self._invoke_tool("llm_query", args, kwargs)
        self.manager.consume_budget()
        active_lm = self.manager.sub_lm or dspy.settings.lm
        with dspy.context(lm=active_lm, disable_history=True):
            return active_lm(args[0])[0]

    def _llm_query_batched(self, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if len(args) != 1:
            raise ValueError("llm_query_batched requires one prompt list")
        prompts = args[0]
        if (
            not isinstance(prompts, list)
            or not prompts
            or len(prompts) > 100
            or any(not isinstance(prompt, str) or len(prompt) > 100_000 for prompt in prompts)
        ):
            raise ValueError("llm_query_batched requires 1-100 bounded string prompts")
        if "llm_query_batched" in self.tools:
            return self._invoke_tool("llm_query_batched", args, kwargs)
        self.manager.consume_budget(len(prompts))
        active_lm = self.manager.sub_lm or dspy.settings.lm

        def complete(prompt: str) -> Any:
            with dspy.context(lm=active_lm, disable_history=True):
                return active_lm(prompt)[0]

        with ThreadPoolExecutor(max_workers=min(8, len(prompts))) as pool:
            return list(pool.map(complete, prompts))

    def _jev_decide_batched(self, args: list[Any], _kwargs: dict[str, Any]) -> Any:
        if len(args) != 3:
            raise ValueError("jev_decide_batched requires items, question, and choices")
        items, question, choices = args
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or len(item) > 100_000 for item in items)
            or not isinstance(question, str)
            or len(question) > 10_000
            or not isinstance(choices, list)
            or any(not isinstance(choice, str) or not choice or len(choice) > 256 for choice in choices)
            or len(set(choices)) != len(choices)
        ):
            raise ValueError("Invalid Jev batch inputs")
        self.manager.consume_budget(len(items))
        return self._run_jev_batch(items, question, choices)

    # Interpreter APIs every namespace has; registered tools with other names go to _invoke_tool.
    _BUILTINS: ClassVar[Mapping[str, Callable[[DaytonaInterpreter, list[Any], dict[str, Any]], Any]]] = {
        "llm_query": _llm_query,
        "llm_query_batched": _llm_query_batched,
        "jev_decide_batched": _jev_decide_batched,
    }

    def _run_jev_batch(self, items: list[str], question: str, choices: list[str]) -> list[dict[str, Any]]:
        if not items or not choices or len(items) > 100 or len(choices) > 20:
            raise ValueError("Jev batch inputs exceed the supported bounds")
        from pydantic import BaseModel

        predictor = dspy.Predict(item_decision(choices))
        active_lm = self.manager.jev_lm or self.manager.sub_lm or dspy.settings.lm
        examples = [dspy.Example(item=item, question=question).with_inputs("item", "question") for item in items]
        callbacks = list(dspy.settings.callbacks)
        if active_lm is self.manager.jev_lm and self.manager.jev_lm is not None:
            callbacks = without_mlflow_dspy_callback(callbacks)
        with mlflow.start_span(name="jev_batch_decisions", span_type=SpanType.LLM) as span:
            span.set_inputs({"item_count": len(items), "question": activity_text(question), "choices": choices})
            with dspy.context(lm=active_lm, disable_history=True, callbacks=callbacks):
                predictions = predictor.batch(examples, num_threads=min(8, len(items)))
            results: list[dict[str, Any]] = []
            for item, prediction in zip(items, predictions, strict=True):
                decision = getattr(prediction, "decision", None)
                choice_value = getattr(decision, "value", None) if isinstance(decision, BaseModel) else None
                if not isinstance(choice_value, str):
                    raise RuntimeError("Jev returned an invalid decision")
                results.append({"item": item, "decision": choice_value, **decision_evidence(decision, active_lm)})
            span.set_outputs({"decisions": [{k: v for k, v in row.items() if k != "item"} for row in results]})
            return results

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        if not isinstance(code, str) or len(code.encode("utf-8")) > 1_000_000:
            raise ValueError("Interpreter code exceeds the 1 MB size limit")
        if variables is not None and not isinstance(variables, dict):
            raise TypeError("Interpreter variables must be an object")
        self.start()
        from dspy.primitives.facade import SHIM_SETUP

        execution_id = uuid.uuid4().hex
        phase = "bootstrap" if code == SHIM_SETUP else "task"
        started = time.monotonic()
        if hasattr(self.manager, "check_turn"):
            self.manager.check_turn()
        if hasattr(self.manager, "emit"):
            self.manager.emit(
                "repl_code",
                code=activity_text(code),
                namespace=self.ns_id,
                execution_id=execution_id,
                phase=phase,
            )
        from dspy.primitives.code_interpreter import CodeExecutionError, FinalOutput

        response: dict[str, Any] = {}
        with mlflow.start_span(name="sandbox.exec", span_type=SpanType.TOOL) as span:
            span.set_attributes(
                {
                    "phase": phase,
                    "namespace": self.ns_id,
                    "execution_id": execution_id,
                    "agent_id": getattr(self.manager, "agent_id", None),
                    "parent_agent_id": getattr(getattr(self.manager, "parent", None), "agent_id", None),
                    "sandbox_id": getattr(getattr(self.manager, "_sandbox", None), "id", None),
                }
            )
            span.set_inputs({"code": activity_text(code)})
            host_calls_before = self._host_calls
            try:
                response = self.manager.run_namespace(
                    self, {"code": code, "variables": variables or {}, "tools": list(self.tools)}
                )
                span.set_outputs(
                    {
                        "stdout": activity_text(str(response.get("stdout", ""))),
                        "stderr": activity_text(str(response.get("stderr", ""))),
                        "value": activity_preview(
                            response.get("final") if response.get("final") is not None else response.get("value")
                        ),
                        "error": bool(response.get("error")),
                    }
                )
                span.set_attribute("transport_retries", response.get("transport_retries", 0))
                if response.get("error"):
                    span.set_attribute("outcome", "remote_code_error")
                    span.set_attribute("remote_error_type", response.get("error_type", "PythonError"))
                    output = str(response.get("stdout", "")) + str(response.get("stderr", ""))
                    raise CodeExecutionError(output[-20_000:] or "Remote Python execution failed")
                span.set_attribute("outcome", "completed")
                if response.get("final") is not None:
                    return FinalOutput(response["final"])
                if response.get("has_value") and response.get("value") is not None:
                    return response["value"]
                return (str(response.get("stdout", "")) + str(response.get("stderr", "")))[-20_000:].strip() or None
            except Exception as exc:
                response.update(error=True, error_type=response.get("error_type") or type(exc).__name__)
                span.set_attribute("transport_retries", getattr(exc, "transport_retries", 0))
                if not isinstance(exc, CodeExecutionError):
                    outcome = (
                        "transport_uncertain"
                        if isinstance(exc, ExecutionOutcomeUnknownError)
                        else (
                            "deadline"
                            if isinstance(exc, TimeoutError)
                            else "cancelled"
                            if getattr(getattr(self.manager, "root", None), "_cancelled", threading.Event()).is_set()
                            else "transport_error"
                        )
                    )
                    span.set_attribute("outcome", outcome)
                raise
            finally:
                span.set_attribute("host_calls", self._host_calls - host_calls_before)
                if hasattr(self.manager, "emit"):
                    self.manager.emit(
                        "repl_output",
                        namespace=self.ns_id,
                        execution_id=execution_id,
                        phase=phase,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        stdout=activity_text(str(response.get("stdout", ""))),
                        stderr=activity_text(str(response.get("stderr", ""))),
                        value=activity_preview(
                            response.get("final") if response.get("final") is not None else response.get("value")
                        ),
                        error=bool(response.get("error")) or not response,
                        error_type=response.get("error_type"),
                    )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            try:
                self.manager.drop_namespace(self.ns_id)
            finally:
                self.manager._unregister_interpreter(self)
        if self.owns_manager:
            children = _NATIVE_CHILDREN.get()
            if children is not None:
                children.append(self.manager)
            else:
                # No invocation can attest success; cleanup must not publish outputs.
                self.manager.shutdown()
