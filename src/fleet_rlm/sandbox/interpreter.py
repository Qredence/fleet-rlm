"""DaytonaInterpreter: DSPy's CodeInterpreter over one namespace of a Session sandbox."""

from __future__ import annotations

import contextvars
import json
import keyword
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, ClassVar

import dspy
import mlflow
from dspy.primitives.facade import BOOTSTRAP, LM_ERROR_KEY
from mlflow.entities import SpanType
from pydantic_core import to_jsonable_python

from fleet_rlm.contracts import decision_evidence, figure_grounded, figures, item_decision, yes_no_decision
from fleet_rlm.observability import activity_preview, activity_text, evidence_span, typed_decision_scope
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, _NATIVE_CHILDREN, DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.workspace import _child_input_path

_LOGGER = logging.getLogger(__name__)

_MAX_OBSERVED_FIGURES = 200_000
_MAX_UNGROUNDED_REJECTIONS = 2


def _unwrap_wire(value: Any) -> Any:
    """Strip DSPy wire envelopes when inspecting guest payloads for grounding or previews."""
    if isinstance(value, dict) and value.keys() == {"__dspy__", "session", "value"} and value["__dspy__"] == "wire":
        return _unwrap_wire(value["value"])
    if isinstance(value, dict):
        return {key: _unwrap_wire(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrap_wire(item) for item in value]
    return value


def _is_bootstrap_code(code: str) -> bool:
    """Classify DSPy internal session setup and wire-injection cells as bootstrap."""
    return (
        code == BOOTSTRAP
        or code == "import dspy"
        or (code.count("\n") == 0 and '__dspy_values.decode(__import__("json").loads(' in code)
    )


def _strip_rebind_prelude(code: str) -> str:
    """Remove RLM's internal per-cell rebind prelude from operator/trace code views."""
    from dspy.predict.rlm import _REBIND

    return code.removeprefix(_REBIND)


class StructuredResponseError(dspy.CodeExecutionError):
    """A host-verified structured response failure that escaped remote execution."""


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
        self._format_failures: dict[str, dict[str, tuple[str, Exception]]] = {}
        # Figures this namespace's inputs and executions have shown the model, for the SUBMIT check.
        self._observed: set[float] = set()
        self._ungrounded_rejections = 0
        self._completed_cleanly = False
        self._session_lm_failed = False

    def start(self) -> None:
        """Start the sandbox and bootstrap this namespace once; raise if already shut down."""
        if self._closed:
            raise RuntimeError("Interpreter has already been shut down")
        self.manager.ensure_sandbox()
        if not self._started:
            self.manager._register_interpreter(self)
            self._started = True

    def _child_workspace_manifest(self) -> str:
        """Return the subset of workspace paths accessible to an isolated child interpreter."""
        paths = sorted(
            path
            for path in self.manager.list_workspace_files()
            if path.startswith("skills/")
            or (path.startswith("files/") if self.native_input_paths is None else path in self.native_input_paths)
        )
        return ", ".join(f"/workspace/{path}" for path in paths) or "/workspace/files (empty)"

    def _rewrite_nested_rlm_prompt(
        self, bound_session: Any, args: list[Any], kwargs: dict[str, Any]
    ) -> tuple[list[Any], dict[str, Any]]:
        """Restrict `variables_info` workspace_manifest previews when an owned child interpreter is open."""
        op = kwargs.get("op") if "op" in kwargs else (args[0] if args else None)
        if op != "adapter":
            return args, kwargs
        nested_map = getattr(bound_session, "_nested", None)
        if not isinstance(nested_map, dict):
            return args, kwargs
        active_child = next(
            (
                entry.interpreter
                for entry in reversed(list(nested_map.values()))
                if isinstance(getattr(entry, "interpreter", None), DaytonaInterpreter)
                and entry.interpreter.owns_manager
            ),
            None,
        )
        if active_child is None:
            return args, kwargs
        prompt = kwargs.get("prompt") if "prompt" in kwargs else (args[1] if len(args) > 1 else None)
        if not isinstance(prompt, dict) or "inputs" not in prompt:
            return args, kwargs
        raw_inputs = prompt["inputs"]
        wrapped = (
            isinstance(raw_inputs, dict)
            and raw_inputs.keys() == {"__dspy__", "session", "value"}
            and raw_inputs["__dspy__"] == "wire"
            and isinstance(raw_inputs["value"], dict)
        )
        inputs_map = raw_inputs["value"] if wrapped else raw_inputs
        if not isinstance(inputs_map, dict):
            return args, kwargs
        raw_info = inputs_map.get("variables_info")
        info_wrapped = (
            isinstance(raw_info, dict)
            and raw_info.keys() == {"__dspy__", "session", "value"}
            and raw_info["__dspy__"] == "wire"
            and isinstance(raw_info["value"], list)
        )
        variables_info = raw_info["value"] if info_wrapped else raw_info
        if not isinstance(variables_info, list):
            return args, kwargs
        prefix = "Variable: `workspace_manifest` (access it in your code)\n"
        if not any(isinstance(item, str) and item.startswith(prefix) for item in variables_info):
            return args, kwargs
        from dspy.primitives.repl_types import REPLVariable

        replacement = REPLVariable.from_value("workspace_manifest", active_child._child_workspace_manifest()).format()
        updated_info = [
            replacement if isinstance(item, str) and item.startswith(prefix) else item for item in variables_info
        ]
        new_info_field = {**raw_info, "value": updated_info} if info_wrapped else updated_info
        new_inputs_map = {**inputs_map, "variables_info": new_info_field}
        new_inputs_field = {**raw_inputs, "value": new_inputs_map} if wrapped else new_inputs_map
        updated_prompt = {**prompt, "inputs": new_inputs_field}
        if "prompt" in kwargs:
            return args, {**kwargs, "prompt": updated_prompt}
        return [args[0], updated_prompt, *args[2:]], kwargs

    def _invoke_tool(self, tool_name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        """The host invocation, not interpreter shutdown, owns child success."""
        children: list[Any] = []
        token = _NATIVE_CHILDREN.set(children)
        bound_session = getattr(self.tools.get(tool_name), "__self__", None)
        closing_child_interpreter: Any = None
        if tool_name == "__dspy_lm__":
            args, kwargs = self._rewrite_nested_rlm_prompt(bound_session, args, kwargs)
        elif tool_name == "__dspy_interpreter__":
            op = kwargs.get("op") if "op" in kwargs else (args[0] if args else None)
            if op == "shutdown":
                handle = kwargs.get("id") if "id" in kwargs else (args[1] if len(args) > 1 else None)
                nested_map = getattr(bound_session, "_nested", None)
                nested_entry = (
                    nested_map.get(handle) if isinstance(nested_map, dict) and isinstance(handle, str) else None
                )
                closing_child_interpreter = getattr(nested_entry, "interpreter", None)
        try:
            try:
                value = self.tools[tool_name](*args, **kwargs)
            except Exception:
                if tool_name == "__dspy_lm__":
                    self._session_lm_failed = True
                raise
            if tool_name == "__dspy_lm__" and isinstance(value, dict) and LM_ERROR_KEY in value:
                self._session_lm_failed = True
            self.manager.check_turn()
            promote = closing_child_interpreter is None or (
                getattr(closing_child_interpreter, "_completed_cleanly", False)
                and not self._session_lm_failed
                and getattr(bound_session, "lm_error", None) is None
            )
            if promote:
                for child in children:
                    child.collect_child_artifacts()
            return value
        finally:
            _NATIVE_CHILDREN.reset(token)
            for child in children:
                child.shutdown()

    def answer_host_call(self, call: dict[str, Any], *, execution_id: str | None = None) -> dict[str, Any]:
        """Run one bridged call in this interpreter's DSPy context; the reply the sandbox is waiting for."""
        return self._context.copy().run(self._handle_host_call, call, execution_id)

    def _handle_host_call(self, call: dict[str, Any], execution_id: str | None = None) -> dict[str, Any]:
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
            if isinstance(exc, (dspy.utils.exceptions.AdapterParseError, StructuredResponseError)):
                receipt = uuid.uuid4().hex
                with self._host_call_lock:
                    failures = self._format_failures.get(execution_id) if execution_id is not None else None
                    if failures is not None and isinstance(call_id, str):
                        failures[receipt] = (call_id, exc)
                        return {
                            "error": "The model returned an invalid structured response.",
                            "host_error_id": receipt,
                        }
                return {"error": "The model returned an invalid structured response."}
            return {"error": str(exc)[:2000]}
        finally:
            _ACTIVE_MANAGER.reset(active_token)

    def _complete_sub_lm(self, prompt: str, active_lm: Any) -> Any:
        with dspy.context(lm=active_lm, disable_history=True):
            result = active_lm(prompt)
            if isinstance(result, dspy.lm15.Response):
                return result.text
            return result[0]

    @staticmethod
    def _valid_prompt(value: Any) -> bool:
        return isinstance(value, str) and len(value) <= 100_000

    def _llm_query(self, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if "llm_query" in self.tools:
            return self._invoke_tool("llm_query", args, kwargs)
        if len(args) != 1 or not self._valid_prompt(args[0]):
            raise ValueError("llm_query requires one prompt of at most 100000 characters")
        self.manager.consume_budget()
        return self._complete_sub_lm(args[0], self.manager.sub_lm or dspy.settings.lm)

    def _llm_query_batched(self, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if "llm_query_batched" in self.tools:
            return self._invoke_tool("llm_query_batched", args, kwargs)
        if len(args) != 1:
            raise ValueError("llm_query_batched requires one prompt list")
        prompts = args[0]
        if (
            not isinstance(prompts, list)
            or not prompts
            or len(prompts) > 100
            or not all(map(self._valid_prompt, prompts))
        ):
            raise ValueError("llm_query_batched requires 1-100 bounded string prompts")
        self.manager.consume_budget(len(prompts))
        active_lm = self.manager.sub_lm or dspy.settings.lm
        with ThreadPoolExecutor(max_workers=min(8, len(prompts))) as pool:
            # Each worker needs the submitting Turn's full context, with its own Context
            # so simultaneous workers cannot enter the same Context concurrently.
            futures = [
                pool.submit(contextvars.copy_context().run, self._complete_sub_lm, prompt, active_lm)
                for prompt in prompts
            ]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
            return [future.result() for future in futures]

    def _jev_decide_batched(self, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if len(args) not in {2, 3}:
            raise ValueError("jev_decide_batched requires items, question, and optional choices")
        items, question = args[0], args[1]
        choices = args[2] if len(args) == 3 else kwargs.get("choices")
        invalid_choices = choices is not None and (
            not isinstance(choices, list)
            or any(not isinstance(choice, str) or not choice or len(choice) > 256 for choice in choices)
            or len(set(choices)) != len(choices)
        )
        if (
            not isinstance(items, list)
            or not all(map(self._valid_prompt, items))
            or not isinstance(question, str)
            or len(question) > 10_000
            or invalid_choices
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

    def _run_jev_batch(self, items: list[str], question: str, choices: list[str] | None = None) -> list[dict[str, Any]]:
        """Classify 1-100 items via Choice (when `choices` is given) or boolean Noul (when `choices` is None).

        Prefer the Jev LM, then the sub LM, then the ambient DSPy LM. Invalid batch
        counts raise ValueError; missing typed decisions raise RuntimeError. Prediction
        and result shape errors propagate.
        """
        if not items or len(items) > 100 or (choices is not None and (not choices or len(choices) > 20)):
            raise ValueError("Jev batch inputs exceed the supported bounds")
        from pydantic import BaseModel

        predictor = dspy.Predict(yes_no_decision() if choices is None else item_decision(choices))
        active_lm = self.manager.jev_lm or self.manager.sub_lm or dspy.settings.lm
        examples = [dspy.Example(item=item, question=question).with_inputs("item", "question") for item in items]
        typesafe = active_lm is self.manager.jev_lm and self.manager.jev_lm is not None
        with evidence_span(
            "jev_batch_decisions",
            inputs={"item_count": len(items), "question": activity_text(question), "choices": choices},
        ) as span:
            with typed_decision_scope(lm=active_lm, span=span, typesafe=typesafe, adapter=self.manager.sub_adapter):
                # Decisions are prepaid; disable DSPy's speculative straggler resubmission.
                predictions = predictor.batch(examples, num_threads=min(8, len(items)), timeout=0)
            results: list[dict[str, Any]] = []
            for item, prediction in zip(items, predictions, strict=True):
                decision = getattr(prediction, "decision", None)
                decision_value = getattr(decision, "value", None) if isinstance(decision, BaseModel) else None
                if choices is None:
                    if not isinstance(decision_value, bool):
                        raise RuntimeError("Jev returned an invalid boolean decision")
                elif not isinstance(decision_value, str):
                    raise RuntimeError("Jev returned an invalid decision")
                results.append({"item": item, "decision": decision_value, **decision_evidence(decision, active_lm)})
            span.set_outputs({"decisions": [{k: v for k, v in row.items() if k != "item"} for row in results]})
            return results

    def _observe(self, text: str) -> None:
        if len(self._observed) < _MAX_OBSERVED_FIGURES:
            self._observed.update(figures(text).values())

    def _check_grounded_submit(self, code: str, final: Any, span: Any, error_type: type[Exception]) -> None:
        """Reject a SUBMIT whose figures were typed into the code but never shown by an execution or input.

        Computed values (formatted from variables) are not code literals and pass. A capped number of
        rejections keeps a legitimate answer from blocking the Turn; past the cap it is accepted and flagged.
        """
        typed = figures(code)
        unwrapped_final = _unwrap_wire(final)

        def unseen(value: Any) -> list[str]:
            return sorted(
                literal
                for literal, number in figures(json.dumps(value, default=str)).items()
                if literal in typed and not figure_grounded(literal, number, self._observed)
            )

        substantive = unwrapped_final
        if isinstance(unwrapped_final, dict) and "learned_skill_note" in unwrapped_final:
            substantive = {key: value for key, value in unwrapped_final.items() if key != "learned_skill_note"}
            note_figures = unseen(unwrapped_final["learned_skill_note"])
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
        # Native Flex and RLM wire encode values into JSON-compatible structures.
        if variables is not None:
            variables = to_jsonable_python(variables)
            if self.owns_manager and isinstance(variables.get("workspace_manifest"), str):
                # Native child sandboxes receive copied files/ and skills/, not the parent's
                # artifact/memory inventory. Do not advertise inaccessible paths.
                variables = {
                    **variables,
                    "workspace_manifest": self._child_workspace_manifest(),
                }
        self.start()
        self._completed_cleanly = False
        if code == BOOTSTRAP:
            self._session_lm_failed = False

        execution_id = uuid.uuid4().hex
        phase = "bootstrap" if _is_bootstrap_code(code) else "task"
        display_code = _strip_rebind_prelude(code) if phase == "task" else code
        started = time.monotonic()
        if phase == "task" and variables:
            self._observe(json.dumps(variables, default=str))
        self.manager.check_turn()
        self.manager.emit(
            "repl_code",
            code=activity_text(display_code),
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
            span.set_inputs({"code": activity_text(display_code)})
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
                with self._host_call_lock:
                    self._format_failures[execution_id] = {}
                response = self.manager.run_namespace(self, payload)
                preview_target = _unwrap_wire(
                    response.get("final") if response.get("final") is not None else response.get("value")
                )
                span.set_outputs(
                    {
                        "skill_activity": response.get("skill_activity", []),
                        "skill_activity_truncated": response.get("skill_activity_truncated", False),
                        "stdout": activity_text(str(response.get("stdout", ""))),
                        "stderr": activity_text(str(response.get("stderr", ""))),
                        "value": activity_preview(preview_target),
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
                    receipt = response.get("host_error_id")
                    with self._host_call_lock:
                        failure = (
                            self._format_failures.get(execution_id, {}).get(receipt)
                            if isinstance(receipt, str)
                            else None
                        )
                    if failure is not None and failure[0] == response.get("host_call_id"):
                        raise StructuredResponseError(
                            "The model returned an invalid structured response."
                        ) from failure[1]
                    raise CodeExecutionError(output[-20_000:] or "Remote Python execution failed")
                # Sample only executions that ran; cancel, deadline, and transport paths never wait on telemetry.
                ran = not response.get("error")
                sample = getattr(self.manager, "resource_sample", lambda: None)() if ran else None
                if sample is not None:
                    span.set_attributes(sample)
                self.manager.check_turn()
                span.set_attribute("outcome", "completed")
                response["outcome"] = "completed"
                if response.get("final") is not None:
                    if phase == "task":
                        self._check_grounded_submit(display_code, response["final"], span, CodeExecutionError)
                    self._completed_cleanly = True
                    return FinalOutput(response["final"])
                self._completed_cleanly = True
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
                with self._host_call_lock:
                    self._format_failures.pop(execution_id, None)
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
                        _unwrap_wire(
                            response.get("final") if response.get("final") is not None else response.get("value")
                        )
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
