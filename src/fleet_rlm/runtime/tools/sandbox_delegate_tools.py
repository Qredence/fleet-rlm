"""Delegate and cached-runtime tool builders for sandbox runtimes."""

from __future__ import annotations

import enum
import keyword
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

try:
    import mlflow as _mlflow
except ImportError:  # pragma: no cover - optional dependency
    mlflow: Any | None = None
else:
    mlflow = _mlflow

from fleet_rlm.runtime.agent.recursive_runtime import spawn_delegate_sub_agent_async
from fleet_rlm.runtime.agent.signatures import GroundedCitation
from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable
from fleet_rlm.runtime.models.builders import VARIABLE_MODE_THRESHOLD

from .llm_tools import coerce_int as _coerce_int
from .llm_tools import coerce_str_list as _coerce_str_list
from .llm_tools import prediction_value as _prediction_value
from .llm_tools import run_cached_runtime_module as _run_runtime_module
from .llm_tools import runtime_metadata as _runtime_metadata
from .shared import (
    build_trajectory_payload,
    resolve_document,
)

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


@dataclass(slots=True)
class _DelegateToolContext:
    """Shared context for RLM delegation tool callables."""

    agent: RLMReActChatAgent


@dataclass(frozen=True, slots=True)
class _ToolRegistration:
    """Compact record for building DSPy tools from local callables."""

    name: str
    desc: str
    func: Any


# ---------------------------------------------------------------------------
# Declarative tool-factory types
# ---------------------------------------------------------------------------


class _CoercionKind(enum.Enum):
    """How to coerce a prediction field into the output payload."""

    STR = "str"
    STR_LIST = "str_list"
    INT = "int"
    DICT_OR_STR_LIST = "dict_or_str_list"
    SEVERITY = "severity"


@dataclass(frozen=True, slots=True)
class _OutputField:
    """Declarative spec for a single output field extracted from a prediction."""

    name: str
    kind: _CoercionKind
    pred_field: str | None = None  # defaults to *name* when None
    default: Any = None
    int_min: int | None = None
    int_max: int | None = None
    severity_values: tuple[str, ...] = ("low", "medium", "high", "critical")
    severity_default: str = "low"

    @property
    def source(self) -> str:
        return self.pred_field if self.pred_field is not None else self.name


@dataclass(frozen=True, slots=True)
class _CachedRuntimeToolSpec:
    """Declarative description of a cached-runtime tool.

    The ``_build_cached_runtime_tool`` factory converts one of these into an
    async callable with the exact signature expected by ``dspy.Tool``.
    """

    # Tool identity
    name: str
    desc: str
    module_name: str

    # Positional parameter names in order.  Defines the public signature of
    # the generated tool so that callers can pass arguments positionally.
    # Include ``alias`` and ``include_trajectory`` if they can be passed
    # positionally; the factory pops them from kwargs after mapping.
    param_order: tuple[str, ...] = ()

    # Parameters forwarded to the runtime module.  Each entry maps a
    # *tool-parameter name* to the *module kwarg name*.  When the value is
    # None the tool parameter name is used as-is for the module kwarg.
    param_mapping: dict[str, str | None] = field(default_factory=dict)
    # Static defaults for optional tool parameters (key=tool-param name).
    # Applied when the parameter is absent from kwargs entirely.
    param_defaults: dict[str, Any] = field(default_factory=dict)
    # Falsy-fallback defaults: applied when the parameter value is falsy
    # (e.g., empty string).  Mirrors the ``value or "default"`` pattern.
    param_falsy_defaults: dict[str, Any] = field(default_factory=dict)

    # Document resolution: when set, the tool gains an ``alias`` parameter
    # and the resolved document text is forwarded under this kwarg name.
    doc_kwarg: str | None = None

    # Whether the tool exposes ``doc_chars`` in the output.
    emit_doc_chars: bool = False

    # Output fields extracted from the prediction.
    output_fields: tuple[_OutputField, ...] = ()


# Sentinel for generated function parameters that should stay required.
_REQUIRED = object()


def _coerce_output_field(prediction: Any, spec: _OutputField) -> Any:
    """Apply a single ``_OutputField`` spec to a prediction."""
    raw = _prediction_value(prediction, spec.source, spec.default)

    if spec.kind is _CoercionKind.STR:
        return str(raw)

    if spec.kind is _CoercionKind.STR_LIST:
        return _coerce_str_list(raw)

    if spec.kind is _CoercionKind.INT:
        return _coerce_int(
            raw,
            default=spec.default or 0,
            minimum=spec.int_min,
            maximum=spec.int_max,
        )

    if spec.kind is _CoercionKind.DICT_OR_STR_LIST:
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
        if isinstance(raw, list):
            return _coerce_str_list(raw)
        return {}

    if spec.kind is _CoercionKind.SEVERITY:
        val = str(raw).strip().lower()
        return val if val in spec.severity_values else spec.severity_default

    return raw  # pragma: no cover - unreachable for known kinds


# ---------------------------------------------------------------------------
# Declarative specs for the four factory-eligible tools
# ---------------------------------------------------------------------------

_SUMMARIZE_LONG_DOCUMENT = _CachedRuntimeToolSpec(
    name="summarize_long_document",
    desc="Summarize a long document with key points and coverage metadata",
    module_name="summarize_long_document",
    param_order=("focus", "alias", "include_trajectory"),
    param_mapping={"focus": None},
    doc_kwarg="document",
    emit_doc_chars=True,
    output_fields=(
        _OutputField("summary", _CoercionKind.STR, default=""),
        _OutputField("key_points", _CoercionKind.STR_LIST, default=[]),
        _OutputField(
            "coverage_pct", _CoercionKind.INT, default=0, int_min=0, int_max=100
        ),
    ),
)

_EXTRACT_FROM_LOGS = _CachedRuntimeToolSpec(
    name="extract_from_logs",
    desc="Extract structured matches and patterns from a loaded log document",
    module_name="extract_from_logs",
    param_order=("query", "alias", "include_trajectory"),
    param_mapping={"query": None},
    doc_kwarg="logs",
    emit_doc_chars=True,
    output_fields=(
        _OutputField("matches", _CoercionKind.STR_LIST, default=[]),
        _OutputField("patterns", _CoercionKind.DICT_OR_STR_LIST, default={}),
        _OutputField("time_range", _CoercionKind.STR, default="unknown"),
    ),
)

_TRIAGE_INCIDENT_LOGS = _CachedRuntimeToolSpec(
    name="triage_incident_logs",
    desc="Triage incident logs and suggest likely causes and recommended actions",
    module_name="triage_incident_logs",
    param_order=("query", "alias", "service_context", "include_trajectory"),
    param_mapping={"query": None, "service_context": None},
    param_defaults={"service_context": ""},
    doc_kwarg="logs",
    emit_doc_chars=False,
    output_fields=(
        _OutputField("severity", _CoercionKind.SEVERITY, default="low"),
        _OutputField("probable_root_causes", _CoercionKind.STR_LIST, default=[]),
        _OutputField("impacted_components", _CoercionKind.STR_LIST, default=[]),
        _OutputField("recommended_actions", _CoercionKind.STR_LIST, default=[]),
        _OutputField("time_range", _CoercionKind.STR, default="unknown"),
    ),
)

_PLAN_CODE_CHANGE = _CachedRuntimeToolSpec(
    name="plan_code_change",
    desc="Produce a code-change plan with files, validation commands, and risks",
    module_name="plan_code_change",
    param_order=("task", "repo_context", "constraints", "include_trajectory"),
    param_mapping={"task": None, "repo_context": None, "constraints": None},
    param_defaults={"repo_context": "", "constraints": ""},
    param_falsy_defaults={"constraints": "Keep changes minimal."},
    output_fields=(
        _OutputField("plan_steps", _CoercionKind.STR_LIST, default=[]),
        _OutputField("files_to_touch", _CoercionKind.STR_LIST, default=[]),
        _OutputField("validation_commands", _CoercionKind.STR_LIST, default=[]),
        _OutputField("risks", _CoercionKind.STR_LIST, default=[]),
    ),
)

_DECLARATIVE_SPECS: tuple[_CachedRuntimeToolSpec, ...] = (
    _SUMMARIZE_LONG_DOCUMENT,
    _EXTRACT_FROM_LOGS,
    _TRIAGE_INCIDENT_LOGS,
    _PLAN_CODE_CHANGE,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _normalize_grounded_citations(value: Any) -> list[GroundedCitation]:
    """Normalize grounded-answer citations into the canonical DSPy shape."""
    if not isinstance(value, list):
        return []

    citations: list[GroundedCitation] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        citation: GroundedCitation = {
            "source": str(item.get("source", "")).strip(),
            "chunk_id": str(item.get("chunk_id", item.get("chunkId", ""))).strip(),
            "evidence": str(item.get("evidence", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
        }
        if any(citation.values()):
            citations.append(citation)

    return citations


def _run_cached_runtime_module(
    ctx: _DelegateToolContext,
    *,
    module_name: str,
    **kwargs: Any,
) -> tuple[Any, dict[str, Any] | None, bool]:
    return _run_runtime_module(
        ctx.agent,
        module_name,
        **kwargs,
    )


def _cached_runtime_success(
    ctx: _DelegateToolContext,
    *,
    prediction: Any,
    fallback_used: bool,
    include_trajectory: bool,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "ok",
        **payload,
        **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        **build_trajectory_payload(prediction, include_trajectory=include_trajectory),
    }


def _record_runtime_failure(
    ctx: _DelegateToolContext,
    error: dict[str, Any] | None,
) -> None:
    if not isinstance(error, dict):
        return
    category = str(error.get("runtime_failure_category", "") or "").strip() or None
    phase = str(error.get("runtime_failure_phase", "") or "").strip() or None
    recorder = getattr(ctx.agent.interpreter, "mark_runtime_degradation", None)
    if callable(recorder):
        recorder(
            category=category or "module_execution_error",
            phase=phase or "execution",
            fallback_used=error.get("delegate_lm_fallback", False),
        )


def _build_tool(registration: _ToolRegistration) -> Any:
    from dspy import Tool

    return Tool(
        _sync_compatible_tool_callable(registration.func),
        name=registration.name,
        desc=registration.desc,
    )


# ---------------------------------------------------------------------------
# Generic factory: spec -> _ToolRegistration
# ---------------------------------------------------------------------------


def _build_cached_runtime_tool(
    ctx: _DelegateToolContext,
    spec: _CachedRuntimeToolSpec,
) -> _ToolRegistration:
    """Convert a ``_CachedRuntimeToolSpec`` into a ``_ToolRegistration``."""

    async def _tool_impl(**kwargs: Any) -> dict[str, Any]:
        include_trajectory: bool = kwargs.pop("include_trajectory", True)

        # -- Document resolution --
        document: str | None = None
        if spec.doc_kwarg is not None:
            alias = kwargs.pop("alias", "active")
            document = resolve_document(ctx.agent, alias)

        # -- Build module kwargs --
        module_kwargs: dict[str, Any] = {}
        if document is not None:
            assert spec.doc_kwarg is not None  # guaranteed by branch above
            module_kwargs[spec.doc_kwarg] = document

        for tool_param, module_kwarg in spec.param_mapping.items():
            dest = module_kwarg if module_kwarg is not None else tool_param
            if tool_param in kwargs:
                value = kwargs[tool_param]
            elif tool_param in spec.param_defaults:
                value = spec.param_defaults[tool_param]
            else:
                continue

            # Apply falsy-fallback default after resolving either an explicit
            # value or a configured default so this mirrors ``val or "default"``.
            if not value and tool_param in spec.param_falsy_defaults:
                value = spec.param_falsy_defaults[tool_param]

            module_kwargs[dest] = value

        # -- Execute --
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx, module_name=spec.module_name, **module_kwargs
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        # -- Coerce output fields --
        payload: dict[str, Any] = {}
        for ofield in spec.output_fields:
            payload[ofield.name] = _coerce_output_field(prediction, ofield)

        if spec.emit_doc_chars and document is not None:
            payload["doc_chars"] = len(document)

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload=payload,
        )

    namespace: dict[str, Any] = {"_tool_impl": _tool_impl}
    param_defs: list[str] = []
    call_args: list[str] = []
    identifiers = (spec.name, *spec.param_order)
    if any(
        not identifier.isidentifier() or keyword.iskeyword(identifier)
        for identifier in identifiers
    ):
        raise ValueError(f"Invalid generated tool signature for {spec.name!r}.")

    for param_name in spec.param_order:
        default: Any = _REQUIRED
        if param_name == "include_trajectory":
            default = True
        elif param_name == "alias" and spec.doc_kwarg is not None:
            default = "active"
        elif param_name in spec.param_defaults:
            default = spec.param_defaults[param_name]

        if default is _REQUIRED:
            param_defs.append(param_name)
        else:
            default_name = f"__default_{param_name}"
            namespace[default_name] = default
            param_defs.append(f"{param_name}={default_name}")
        call_args.append(f"{param_name}={param_name}")

    tool_src = (
        f"async def {spec.name}({', '.join(param_defs)}):\n"
        f"    return await _tool_impl({', '.join(call_args)})\n"
    )
    allowed_namespace_keys = {"_tool_impl"} | {
        f"__default_{param_name}"
        for param_name in spec.param_order
        if param_name == "include_trajectory"
        or (param_name == "alias" and spec.doc_kwarg is not None)
        or param_name in spec.param_defaults
    }
    if set(namespace) != allowed_namespace_keys:
        raise ValueError(f"Unexpected generated tool namespace for {spec.name!r}.")
    # ``spec`` values are module-owned declarative constants, so validated
    # identifiers here stay within trusted repo code rather than user input.
    exec(tool_src, namespace)
    _tool_fn = namespace[spec.name]

    # Preserve the tool name for introspection / debugging.
    _tool_fn.__name__ = spec.name
    _tool_fn.__qualname__ = f"_build_cached_runtime_tool.<locals>.{spec.name}"

    return _ToolRegistration(name=spec.name, desc=spec.desc, func=_tool_fn)


# ---------------------------------------------------------------------------
# Variable-mode RLM routing (Algorithm 1, arXiv 2512.24601v2)
# ---------------------------------------------------------------------------

_VARIABLE_MODE_THRESHOLD = VARIABLE_MODE_THRESHOLD


def _has_interpreter(agent: RLMReActChatAgent) -> bool:
    """Check whether the agent has a started interpreter for variable mode."""
    interp = getattr(agent, "interpreter", None)
    return interp is not None and getattr(interp, "_started", False)


async def _variable_mode_rlm_query(
    ctx: _DelegateToolContext, query: str, context: str
) -> dict[str, Any]:
    """Route a long prompt through true-RLM variable-mode execution.

    Uses ``RLMVariableExecutionModule`` which passes the prompt as a REPL
    variable.  The LLM sees only metadata (type, length, preview) and
    writes code to explore, filter, and sub_rlm() over the data.
    """
    import logging

    from fleet_rlm.runtime.models.builders import build_variable_mode_rlm

    logger = logging.getLogger(__name__)
    interp = ctx.agent.interpreter

    task = query if not context else f"{query}\n\nContext:\n{context}"
    prompt = context if context and len(context) > len(query) else query

    try:
        module = build_variable_mode_rlm(
            interpreter=interp,
            max_iterations=20,
            max_llm_calls=50,
            verbose=bool(getattr(ctx.agent, "verbose", False)),
            sub_lm=getattr(interp, "sub_lm", None),
        )
        prediction = module(task=task, prompt=prompt)
        answer = str(getattr(prediction, "answer", "") or "")
        return {
            "status": "ok",
            "answer": answer,
            "variable_mode": True,
            "prompt_length": len(prompt),
        }
    except Exception as exc:
        logger.warning("variable-mode RLM failed, falling back: %s", exc)
        # Fall back to standard delegation
        result = await spawn_delegate_sub_agent_async(
            ctx.agent,
            prompt=query,
            context=context,
            stream_event_callback=getattr(ctx.agent, "_live_event_callback", None),
        )
        if result.get("status") == "error":
            return result
        return {
            "status": "ok",
            "answer": result.get("answer") or result.get("assistant_response", ""),
            "sub_agent_history": result.get("sub_agent_history", 0),
            "depth": result.get("depth", getattr(ctx.agent, "_current_depth", 0) + 1),
            **build_trajectory_payload(result, include_trajectory=True),
        }


def build_rlm_delegate_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build cached-runtime and recursive delegation tools bound to *agent*."""
    ctx = _DelegateToolContext(agent=agent)

    # -- Factory-generated tools (declarative specs) -------------------------
    declarative_registrations = [
        _build_cached_runtime_tool(ctx, spec) for spec in _DECLARATIVE_SPECS
    ]

    # -- Hand-written tools (custom logic not suited to the factory) ---------

    async def grounded_answer(
        query: str,
        alias: str = "active",
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        max_chunks_int = _coerce_int(max_chunks, default=-1)
        if max_chunks_int <= 0:
            return {"status": "error", "error": "Invalid max_chunks value."}

        document = resolve_document(ctx.agent, alias)
        tracer = getattr(mlflow, "start_span", None) if mlflow is not None else None
        span_ctx = (
            tracer(name="grounded_answer", span_type="RETRIEVER")
            if callable(tracer)
            else nullcontext()
        )
        with span_ctx:
            prediction, error, fallback_used = _run_cached_runtime_module(
                ctx,
                module_name="grounded_answer",
                document=document,
                query=query,
                chunk_strategy=chunk_strategy,
                max_chunks=max_chunks_int,
                response_style="concise",
            )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        citations = _normalize_grounded_citations(
            _prediction_value(prediction, "citations", [])
        )

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
                "answer": str(_prediction_value(prediction, "answer", "")),
                "citations": citations,
                "confidence": _coerce_int(
                    _prediction_value(prediction, "confidence", 0),
                    default=0,
                    minimum=0,
                    maximum=100,
                ),
                "coverage_notes": str(
                    _prediction_value(prediction, "coverage_notes", "")
                ),
                "doc_chars": len(document),
            },
        )

    async def propose_core_memory_update(
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        turn_lines = [
            f"Turn {idx}\n{turn}"
            for idx, turn in enumerate(ctx.agent.history_messages()[-20:], 1)
        ]
        turn_history = "\n\n".join(turn_lines) or "No recent turns."
        prediction, error, fallback_used = _run_cached_runtime_module(
            ctx,
            module_name="propose_core_memory_update",
            turn_history=turn_history,
            current_memory=ctx.agent.fmt_core_memory(),
        )
        if error is not None:
            _record_runtime_failure(ctx, error)
            return error

        return _cached_runtime_success(
            ctx,
            prediction=prediction,
            fallback_used=fallback_used,
            include_trajectory=include_trajectory,
            payload={
                "keep": _coerce_str_list(_prediction_value(prediction, "keep", [])),
                "update": _coerce_str_list(_prediction_value(prediction, "update", [])),
                "remove": _coerce_str_list(_prediction_value(prediction, "remove", [])),
                "rationale": str(_prediction_value(prediction, "rationale", "")),
            },
        )

    async def rlm_query(query: str, context: str = "") -> dict[str, Any]:
        # Auto-route long prompts through true-RLM variable mode
        # (Algorithm 1, arXiv 2512.24601v2): prompt stored as REPL
        # variable, LLM sees only metadata and explores via code.
        combined_len = len(query) + len(context)
        if combined_len > _VARIABLE_MODE_THRESHOLD and _has_interpreter(ctx.agent):
            return await _variable_mode_rlm_query(ctx, query, context)

        result = await spawn_delegate_sub_agent_async(
            ctx.agent,
            prompt=query,
            context=context,
            stream_event_callback=getattr(ctx.agent, "_live_event_callback", None),
        )
        if result.get("status") == "error":
            return result
        return {
            "status": "ok",
            "answer": result.get("answer") or result.get("assistant_response", ""),
            "sub_agent_history": result.get("sub_agent_history", 0),
            "depth": result.get("depth", getattr(ctx.agent, "_current_depth", 0) + 1),
            **build_trajectory_payload(result, include_trajectory=True),
        }

    # -- Build registration list ---------------------------------------------
    hand_written_registrations = [
        _ToolRegistration(
            name="grounded_answer",
            desc="Answer a question with grounded citations from a loaded document",
            func=grounded_answer,
        ),
        _ToolRegistration(
            name="propose_core_memory_update",
            desc="Suggest keep/update/remove actions for core memory after recent conversation turns",
            func=propose_core_memory_update,
        ),
        _ToolRegistration(
            name="rlm_query",
            desc="Run a bounded recursive sub-agent query in a fresh child runtime and return the answer",
            func=rlm_query,
        ),
    ]

    registrations = declarative_registrations + hand_written_registrations
    tools: list[Any] = [_build_tool(registration) for registration in registrations]

    # Batch tools (parallel_semantic_map, rlm_query_batched) from batch_tools
    from .batch_tools import build_batch_tools

    batch_prepend, batch_append = build_batch_tools(agent)
    tools = batch_prepend + tools + batch_append

    return tools
