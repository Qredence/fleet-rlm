"""DaytonaInterpreter: DSPy's CodeInterpreter over one namespace of a Session sandbox."""

from __future__ import annotations

import contextvars
import inspect
import json
import keyword
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from typing import Any, ClassVar

import dspy
import mlflow
from mlflow.entities import SpanType
from pydantic_core import to_jsonable_python

from fleet_rlm.dspy_support import decision_evidence, without_mlflow_dspy_callback
from fleet_rlm.engine.signatures import item_decision
from fleet_rlm.lm_usage import decision_usage
from fleet_rlm.observability import activity_preview, activity_text
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, _NATIVE_CHILDREN, DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.workspace import _child_input_path

_LOGGER = logging.getLogger(__name__)

# A figure: an optionally signed number, with `$` and thousands separators allowed; not part of an identifier.
_FIGURE = re.compile(r"(?<![\w.])\$?-?\d[\d,]*(?:\.\d+)?")
_MAX_OBSERVED_FIGURES = 200_000
_MAX_UNGROUNDED_REJECTIONS = 2


def _figures(text: str) -> dict[str, float]:
    """Normalized number literals in `text`; small integers carry no evidence and are ignored."""
    found: dict[str, float] = {}
    for match in _FIGURE.finditer(text):
        literal = match.group().replace("$", "").replace(",", "")
        try:
            value = float(literal)
        except ValueError:
            continue
        if "." in literal or abs(value) >= 10:
            found[literal] = value
    return found


def _grounded(literal: str, value: float, observed: set[float]) -> bool:
    """A typed figure is grounded when some observed value rounds to it at the typed precision."""
    places = len(literal.split(".", 1)[1]) if "." in literal else 0
    return any(round(seen, places) == round(value, places) for seen in observed)


class DaytonaInterpreter:
    """DSPy CodeInterpreter backed by a namespace in a Session-isolated sandbox."""

    def __init__(
        self,
        manager: DaytonaSandboxManager,
        tools: dict[str, Callable[..., Any]] | None = None,
        owns_manager: bool = False,
        native_input_paths: tuple[str, ...] | None = None,
    ):
        """Create an interpreter with a fresh namespace and validated bridged tool names."""
        self.manager = manager
        self.owns_manager = owns_manager
        self.native_input_paths = (
            tuple(_child_input_path(path) for path in native_input_paths) if native_input_paths is not None else None
        )
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
            "progress_scope",
        }
        if any(
            not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name in reserved
            for name in self.tools
        ):
            raise ValueError("Bridged tool names must be unreserved Python identifiers")
        # DSPy's RLM sets this before each run so SUBMIT takes the signature's output fields.
        self.output_fields: list[dict[str, Any]] | None = None
        self.ns_id = f"ns_{uuid.uuid4().hex[:8]}"
        self._context = contextvars.copy_context()
        self._started = False
        self._closed = False
        self._host_calls = 0
        self._host_call_lock = threading.Lock()
        # Figures this namespace's inputs and executions have shown the model, for the SUBMIT check.
        self._observed: set[float] = set()
        self._ungrounded_rejections = 0
        self._code_agent_handles: set[str] = set()

    def start(self) -> None:
        """Start the sandbox and bootstrap this namespace once; raise if already shut down."""
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
            if tool_name == "__dspy_call__" and kwargs.get("handle") in self._code_agent_handles:
                inputs = kwargs.get("inputs")
                if isinstance(inputs, dict) and isinstance(inputs.get("workspace_manifest"), str):
                    # Native code agents get fresh sandboxes with files/ and skills/, not
                    # the root's artifact/memory inventory. Do not advertise inaccessible paths.
                    paths = sorted(
                        path
                        for path in self.manager.list_workspace_files()
                        if path.startswith("skills/")
                        or (
                            path.startswith("files/")
                            if self.native_input_paths is None
                            else path in self.native_input_paths
                        )
                    )
                    kwargs = {
                        **kwargs,
                        "inputs": {
                            **inputs,
                            "workspace_manifest": ", ".join(f"/workspace/{path}" for path in paths)
                            or "/workspace/files (empty)",
                        },
                    }
            value = self.tools[tool_name](*args, **kwargs)
            if tool_name == "__dspy_construct__" and isinstance(value, str):
                self._code_agent_handles.discard(value)
                cls = getattr(dspy, str(kwargs.get("kind", "")), None)
                if cls is not None and "interpreter_factory" in inspect.signature(cls.__init__).parameters:
                    self._code_agent_handles.add(value)
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
        """Return a bridged tool value or convert validation, cancellation, and tool errors to an error reply.

        Records the host call and emits activity events. Event-delivery failures can propagate.
        """
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
            # Inside the conversion region: a cancelled Turn answers promptly instead of
            # invoking the tool, and the sandbox gets an error reply rather than a hang.
            self.manager.check_turn()
            builtin = self._BUILTINS.get(str(tool_name))
            if builtin is not None:
                value = builtin(self, args, kwargs)
            elif tool_name in self.tools:
                value = self._invoke_tool(tool_name, args, kwargs)
            else:
                raise KeyError(f"Unknown bridged tool: {tool_name}")
            self.manager.emit(
                "tool_end",
                **details,
                ok=True,
                duration_ms=round((time.monotonic() - started) * 1000),
                output="[predictor payload omitted]" if predictor_call else activity_preview(value),
            )
            return {"value": value}
        except Exception as exc:
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
        self.manager.consume_budget(len(items), category="other_decisions")
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
            with (
                dspy.context(lm=active_lm, disable_history=True, callbacks=callbacks),
                decision_usage(span) if active_lm is self.manager.jev_lm else nullcontext(),
            ):
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

    def _observe(self, text: str) -> None:
        if len(self._observed) < _MAX_OBSERVED_FIGURES:
            self._observed.update(_figures(text).values())

    def _check_grounded_submit(self, code: str, final: Any, span: Any, error_type: type[Exception]) -> None:
        """Reject a SUBMIT whose figures were typed into the code but never shown by an execution or input.

        Computed values (formatted from variables) are not code literals and pass. A capped number of
        rejections keeps a legitimate answer from blocking the Turn; past the cap it is accepted and flagged.
        """
        typed = _figures(code)

        def unseen(value: Any) -> list[str]:
            return sorted(
                literal
                for literal, number in _figures(json.dumps(value, default=str)).items()
                if literal in typed and not _grounded(literal, number, self._observed)
            )

        substantive = final
        if isinstance(final, dict) and "learned_skill_note" in final:
            substantive = {key: value for key, value in final.items() if key != "learned_skill_note"}
            note_figures = unseen(final["learned_skill_note"])
            if note_figures:
                span.set_attribute(
                    "learned_note_warning", {"field": "learned_skill_note", "figures": note_figures[:20]}
                )
                self.manager.emit(
                    "learned_note_warning",
                    namespace=self.ns_id,
                    field="learned_skill_note",
                    figures=note_figures[:20],
                )
        ungrounded = unseen(substantive)
        if not ungrounded:
            return
        rejected = self._ungrounded_rejections < _MAX_UNGROUNDED_REJECTIONS
        span.set_attribute("ungrounded_submit", True)
        self.manager.emit("ungrounded_submit", namespace=self.ns_id, figures=ungrounded[:20], rejected=rejected)
        if rejected:
            self._ungrounded_rejections += 1
            self.manager.record_submit_rejection()
            span.set_attribute("outcome", "ungrounded_submit_rejected")
            raise error_type(
                f"SUBMIT rejected: figures {ungrounded[:20]} were typed, not taken from sandbox output. "
                "Compute or print them in code, then SUBMIT values from variables."
            )

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        """Run code in this namespace, rejecting code over 1 MB and non-object variables.

        Return a DSPy FinalOutput for SUBMIT, otherwise the last expression's value when
        non-None, bounded stripped stdout/stderr, or None when there is no output.

        Raises:
            ValueError: Code is not text or exceeds 1,000,000 UTF-8 bytes.
            TypeError: Variables are supplied as a non-dictionary value.
            dspy.CodeExecutionError: Remote Python failed or SUBMIT failed its grounding check.
            TimeoutError: The Turn deadline prevents or interrupts execution.
            RuntimeError: The Turn was cancelled.
            ExecutionOutcomeUnknownError: The execution outcome cannot be established.

        Serialization, setup, and event-delivery errors also propagate.
        """
        if not isinstance(code, str) or len(code.encode("utf-8")) > 1_000_000:
            raise ValueError("Interpreter code exceeds the 1 MB size limit")
        if variables is not None and not isinstance(variables, dict):
            raise TypeError("Interpreter variables must be an object")
        # Native Flex uses DSPy Type serializers and restores their original host
        # objects at facade calls. Do not replace unsupported objects with str().
        if variables is not None:
            variables = to_jsonable_python(variables)
        self.start()
        from dspy.primitives.facade import SHIM_SETUP

        execution_id = uuid.uuid4().hex
        phase = "bootstrap" if code == SHIM_SETUP else "task"
        started = time.monotonic()
        if phase == "task" and variables:
            self._observe(json.dumps(variables, default=str))
        self.manager.check_turn()
        self.manager.emit(
            "repl_code",
            code=activity_text(code),
            namespace=self.ns_id,
            execution_id=execution_id,
            phase=phase,
        )
        from dspy import CodeExecutionError, FinalOutput

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
                payload: dict[str, Any] = {
                    "code": code,
                    "variables": variables or {},
                    "tools": list(self.tools),
                    "execution_id": execution_id,
                }
                if self.output_fields:
                    payload["outputs"] = [field["name"] for field in self.output_fields]
                response = self.manager.run_namespace(self, payload)
                span.set_outputs(
                    {
                        "skill_activity": response.get("skill_activity", []),
                        "skill_activity_truncated": response.get("skill_activity_truncated", False),
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
                    response["outcome"] = "remote_code_error"
                    span.set_attribute("outcome", "remote_code_error")
                    self.manager.record_sandbox_error()
                    span.set_attribute("remote_error_type", response.get("error_type", "PythonError"))
                    output = str(response.get("stdout", "")) + str(response.get("stderr", ""))
                    raise CodeExecutionError(output[-20_000:] or "Remote Python execution failed")
                span.set_attribute("outcome", "completed")
                response["outcome"] = "completed"
                if response.get("final") is not None:
                    if phase == "task":
                        self._check_grounded_submit(code, response["final"], span, CodeExecutionError)
                    return FinalOutput(response["final"])
                if response.get("has_value") and response.get("value") is not None:
                    return response["value"]
                return (str(response.get("stdout", "")) + str(response.get("stderr", "")))[-20_000:].strip() or None
            except Exception as exc:
                if isinstance(exc, CodeExecutionError) and response.get("outcome") == "completed":
                    response["outcome"] = "ungrounded_submit_rejected"
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
                    response["outcome"] = outcome
                raise
            finally:
                if phase == "task":
                    self._observe(f"{response.get('stdout', '')}\n{response.get('value', '')}")
                span.set_attribute("host_calls", self._host_calls - host_calls_before)
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
                    outcome=response.get("outcome"),
                )

    def shutdown(self) -> None:
        """Drop this namespace and release the manager once; later calls are no-ops."""
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
