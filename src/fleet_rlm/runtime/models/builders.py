"""Builder functions and concrete module classes for DSPy runtime modules."""

from __future__ import annotations

import sys as _sys
from dataclasses import dataclass
from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import (
    ClarificationQuestionSignature,
    GroundedAnswerWithCitations,
    MemoryActionIntentSignature,
    MemoryStructureAuditSignature,
    MemoryStructureMigrationPlanSignature,
    RecursiveSubQuerySignature,
    RLMVariableSignature,
    VolumeFileTreeSignature,
)
from fleet_rlm.runtime.content.chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)


def create_runtime_rlm(
    *,
    signature: type[dspy.Signature],
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    max_output_chars: int | None = None,
    verbose: bool,
    tools: list[Any] | None = None,
    sub_lm: dspy.LM | None = None,
) -> dspy.Module:
    """Create a canonical RLM instance for a runtime signature."""

    kwargs: dict[str, Any] = {
        "signature": signature,
        "interpreter": interpreter,
        "max_iterations": max_iterations,
        "max_llm_calls": max_llm_calls,
        "verbose": verbose,
    }
    if max_output_chars is not None:
        kwargs["max_output_chars"] = max_output_chars
    if tools is not None:
        kwargs["tools"] = tools
    if sub_lm is not None:
        kwargs["sub_lm"] = sub_lm

    return dspy.RLM(
        **kwargs,
    )


def build_recursive_subquery_rlm(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    max_output_chars: int | None = None,
    verbose: bool,
    sub_lm: dspy.LM | None = None,
) -> dspy.Module:
    """Build the canonical recursive child-query RLM."""

    return create_runtime_rlm(
        signature=RecursiveSubQuerySignature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        max_output_chars=max_output_chars,
        verbose=verbose,
        sub_lm=sub_lm,
    )


# ---------------------------------------------------------------------------
# True-RLM variable-mode execution (Algorithm 1, arXiv 2512.24601v2)
# ---------------------------------------------------------------------------
# dspy.RLM natively:
#   1. Stores all InputField values as REPL variables (_build_variables)
#   2. Shows only metadata (type, length, preview) to the LLM
#   3. Provides llm_query() and SUBMIT() built-in
#   4. Accepts custom tools= for additional callables (sub_rlm, etc.)
# See https://dspy.ai/api/modules/RLM/
# ---------------------------------------------------------------------------

# Threshold (chars) above which rlm_query auto-routes to variable mode
VARIABLE_MODE_THRESHOLD = 32_000

# Lower max_output_chars for variable-mode forces the LLM to use
# variables (peek, grep, sub_rlm) instead of printing large output.
# dspy.RLM's REPLEntry.format() already shows "Output (N chars):" as
# metadata — this keeps it short so the LLM relies on REPL state.
VARIABLE_MODE_MAX_OUTPUT_CHARS = 5_000


class RLMVariableExecutionModule(dspy.Module):
    """Variable-mode RLM wrapper that preserves the caller's signature.

    This thin wrapper:
    1. Collects ``sub_rlm`` / ``sub_rlm_batched`` from the interpreter
       and registers them as ``dspy.Tool`` instances on the inner RLM.
    2. Reuses the requested DSPy signature so cached runtime-module callers
       keep their existing input/output field names.
    3. Relies on ``dspy.RLM``'s native variable handling to store each input
       field as a REPL variable while exposing only metadata/previews to the LM.

    All heavy lifting (REPL loop, metadata display, iteration budget,
    llm_query) is handled by ``dspy.RLM`` itself.
    """

    def __init__(
        self,
        *,
        signature: type[dspy.Signature] = RLMVariableSignature,
        interpreter: Any,
        max_iterations: int = 20,
        max_llm_calls: int = 50,
        verbose: bool = False,
        max_output_chars: int | None = None,
        sub_lm: dspy.LM | None = None,
        extra_tools: list[Any] | None = None,
    ) -> None:
        super().__init__()
        # Gather sub_rlm tools from the interpreter (if it exposes them)
        tools: list[Any] = list(extra_tools or [])
        for attr_name in ("sub_rlm", "sub_rlm_batched"):
            fn = getattr(interpreter, attr_name, None)
            if callable(fn):
                tools.append(fn)

        self._rlm = create_runtime_rlm(
            signature=signature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            # Use a tighter output limit for variable mode to force the LLM
            # to work through REPL variables rather than printing large output.
            max_output_chars=max_output_chars or VARIABLE_MODE_MAX_OUTPUT_CHARS,
            verbose=verbose,
            tools=tools or None,
            sub_lm=sub_lm,
        )

    def forward(self, **kwargs: Any) -> dspy.Prediction:
        """Run a true-RLM loop while preserving the caller's DSPy fields.

        ``dspy.RLM`` stores each input field as a REPL variable and the
        model writes code to explore those variables before calling
        ``SUBMIT(...)`` with the signature's declared outputs.
        """
        return self._rlm(**kwargs)


def build_variable_mode_rlm(
    *,
    signature: type[dspy.Signature] = RLMVariableSignature,
    interpreter: Any,
    max_iterations: int = 20,
    max_llm_calls: int = 50,
    verbose: bool = False,
    max_output_chars: int | None = None,
    sub_lm: dspy.LM | None = None,
    extra_tools: list[Any] | None = None,
) -> RLMVariableExecutionModule:
    """Factory for the true-RLM variable-mode execution module.

    Use for any task where one or more large inputs should stay in the REPL
    instead of the model context. The LLM sees only metadata and explores
    through code + sub_rlm() recursion.
    """
    return RLMVariableExecutionModule(
        signature=signature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
        max_output_chars=max_output_chars,
        sub_lm=sub_lm,
        extra_tools=extra_tools,
    )


@dataclass(frozen=True)
class RuntimeModuleBuildConfig:
    """Shared constructor parameters for runtime-module RLMs."""

    interpreter: Any
    max_iterations: int
    max_llm_calls: int
    verbose: bool
    max_output_chars: int | None = None
    sub_lm: dspy.LM | None = None


def build_runtime_module_config(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    verbose: bool,
    max_output_chars: int | None = None,
    sub_lm: dspy.LM | None = None,
) -> RuntimeModuleBuildConfig:
    return RuntimeModuleBuildConfig(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
        max_output_chars=max_output_chars,
        sub_lm=sub_lm,
    )


def _create_configured_runtime_rlm(
    config: RuntimeModuleBuildConfig,
    *,
    signature: type[dspy.Signature],
) -> dspy.Module:
    # Resolve through the compat shim so unittest.mock.patch takes effect.
    _mod = _sys.modules.get(
        "fleet_rlm.runtime.models.rlm_runtime_modules", _sys.modules[__name__]
    )
    _create = getattr(_mod, "create_runtime_rlm", create_runtime_rlm)
    return _create(
        signature=signature,
        interpreter=config.interpreter,
        max_iterations=config.max_iterations,
        max_llm_calls=config.max_llm_calls,
        max_output_chars=config.max_output_chars,
        verbose=config.verbose,
        sub_lm=config.sub_lm,
    )


def _normalize_chunk_strategy(strategy: str) -> str:
    normalized = str(strategy).strip().lower().replace("-", "_")
    mapping = {
        "size": "size",
        "headers": "headers",
        "header": "headers",
        "timestamps": "timestamps",
        "timestamp": "timestamps",
        "json": "json_keys",
        "json_keys": "json_keys",
    }
    if normalized not in mapping:
        raise ValueError(
            "Unsupported strategy. Choose one of: size, headers, timestamps, json_keys"
        )
    return mapping[normalized]


def _chunk_document(text: str, strategy: str) -> list[Any]:
    strategy_norm = _normalize_chunk_strategy(strategy)
    if strategy_norm == "size":
        return chunk_by_size(text, size=80_000, overlap=1_000)
    if strategy_norm == "headers":
        return chunk_by_headers(text, pattern=r"^#{1,3} ")
    if strategy_norm == "timestamps":
        return chunk_by_timestamps(text, pattern=r"^\d{4}-\d{2}-\d{2}[T ]")
    return chunk_by_json_keys(text)


def _chunk_to_text(chunk: Any) -> str:
    if isinstance(chunk, str):
        return chunk
    if not isinstance(chunk, dict):
        return str(chunk)
    if "header" in chunk:
        return f"{chunk.get('header', '')}\n{chunk.get('content', '')}".strip()
    if "timestamp" in chunk:
        return str(chunk.get("content", ""))
    if "key" in chunk:
        return f"{chunk.get('key', '')}\n{chunk.get('content', '')}".strip()
    return str(chunk)


def _coerce_bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


class GroundedAnswerSynthesisModule(dspy.Module):
    """Compose chunking + evidence selection before the grounded-answer RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._grounded_answer = _create_configured_runtime_rlm(
            build_runtime_module_config(
                interpreter=interpreter,
                max_iterations=max_iterations,
                max_llm_calls=max_llm_calls,
                verbose=verbose,
            ),
            signature=GroundedAnswerWithCitations,
        )

    def forward(
        self,
        *,
        document: str,
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        response_style: str = "concise",
    ) -> dspy.Prediction:
        max_chunks_int = _coerce_bounded_int(max_chunks, default=24, minimum=1)
        try:
            chunks = _chunk_document(document, chunk_strategy)
        except ValueError as exc:
            return dspy.Prediction(
                answer="",
                citations=[],
                confidence=0,
                coverage_notes=str(exc),
            )

        evidence_chunks = [_chunk_to_text(chunk) for chunk in chunks][:max_chunks_int]
        if not evidence_chunks:
            return dspy.Prediction(
                answer="",
                citations=[],
                confidence=0,
                coverage_notes="No evidence chunks available.",
            )

        return self._grounded_answer(
            query=query,
            evidence_chunks=evidence_chunks,
            response_style=response_style,
        )


class _MemoryTreePrimedModule(dspy.Module):
    """Base helper for modules that prime work with a memory tree snapshot."""

    def __init__(self, *, config: RuntimeModuleBuildConfig) -> None:
        super().__init__()
        self._memory_tree = _create_configured_runtime_rlm(
            config,
            signature=VolumeFileTreeSignature,
        )

    def _resolve_tree_snapshot(
        self,
        *,
        root_path: str,
        max_depth: int,
        include_hidden: bool,
        tree_snapshot: list[Any] | None,
    ) -> list[Any]:
        if tree_snapshot is not None:
            return list(tree_snapshot or [])

        tree_prediction = self._memory_tree(
            root_path=root_path,
            max_depth=_coerce_bounded_int(max_depth, default=4, minimum=0, maximum=12),
            include_hidden=bool(include_hidden),
        )
        return list(getattr(tree_prediction, "nodes", []) or [])

    def _resolve_tree_context(
        self,
        *,
        root_path: str,
        max_depth: int,
        include_hidden: bool,
        available_context: str,
    ) -> str:
        context_text = available_context.strip()
        if context_text:
            return context_text

        tree_nodes = self._resolve_tree_snapshot(
            root_path=root_path,
            max_depth=max_depth,
            include_hidden=include_hidden,
            tree_snapshot=None,
        )[:20]
        return f"memory_root={root_path}; nodes_sample={tree_nodes}"


class MemoryStructureAuditPlanningModule(_MemoryTreePrimedModule):
    """Compose a memory-tree snapshot with the audit RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        config = build_runtime_module_config(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        super().__init__(config=config)
        self._memory_structure_audit = _create_configured_runtime_rlm(
            config,
            signature=MemoryStructureAuditSignature,
        )

    def forward(
        self,
        *,
        usage_goals: str = "Keep memory discoverable, consistent, and easy to maintain.",
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
        tree_snapshot: list[Any] | None = None,
    ) -> dspy.Prediction:
        return self._memory_structure_audit(
            tree_snapshot=self._resolve_tree_snapshot(
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
                tree_snapshot=tree_snapshot,
            ),
            usage_goals=usage_goals,
        )


class MemoryActionPlanningModule(_MemoryTreePrimedModule):
    """Compose a memory-tree snapshot with the action-intent RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        config = build_runtime_module_config(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        super().__init__(config=config)
        self._memory_action_intent = _create_configured_runtime_rlm(
            config,
            signature=MemoryActionIntentSignature,
        )

    def forward(
        self,
        *,
        user_request: str,
        policy_constraints: str = "Prefer non-destructive operations and ask for confirmation on risky actions.",
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
        current_tree: list[Any] | None = None,
    ) -> dspy.Prediction:
        return self._memory_action_intent(
            user_request=user_request,
            current_tree=self._resolve_tree_snapshot(
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
                tree_snapshot=current_tree,
            ),
            policy_constraints=policy_constraints,
        )


class MemoryMigrationPlanningModule(dspy.Module):
    """Compose memory audit + migration planning into one runtime module."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        config = build_runtime_module_config(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        # Resolve through the backwards-compat shim when loaded so that
        # ``unittest.mock.patch`` on that module takes effect.
        _mod = _sys.modules.get(
            "fleet_rlm.runtime.models.rlm_runtime_modules", _sys.modules[__name__]
        )
        _AuditCls = getattr(
            _mod,
            "MemoryStructureAuditPlanningModule",
            MemoryStructureAuditPlanningModule,
        )
        self._memory_structure_audit = _AuditCls(
            interpreter=config.interpreter,
            max_iterations=config.max_iterations,
            max_llm_calls=config.max_llm_calls,
            verbose=config.verbose,
        )
        self._memory_structure_migration_plan = _create_configured_runtime_rlm(
            config,
            signature=MemoryStructureMigrationPlanSignature,
        )

    def forward(
        self,
        *,
        approved_constraints: str = "No destructive operation without explicit confirmation and rollback.",
        usage_goals: str = "Keep memory discoverable, consistent, and easy to maintain.",
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
        audit_findings: list[str] | None = None,
    ) -> dspy.Prediction:
        if audit_findings is None:
            audit_prediction = self._memory_structure_audit(
                usage_goals=usage_goals,
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
            )
            audit_findings = list(getattr(audit_prediction, "issues", []) or [])

        return self._memory_structure_migration_plan(
            audit_findings=list(audit_findings or []),
            approved_constraints=approved_constraints,
        )


class ClarificationQuestionPlanningModule(_MemoryTreePrimedModule):
    """Compose memory context gathering with clarification-question generation."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        config = build_runtime_module_config(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        super().__init__(config=config)
        self._clarification_questions = _create_configured_runtime_rlm(
            config,
            signature=ClarificationQuestionSignature,
        )

    def forward(
        self,
        *,
        request: str,
        operation_risk: str = "medium",
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
        available_context: str = "",
    ) -> dspy.Prediction:
        risk_norm = str(operation_risk).strip().lower()
        if risk_norm not in {"low", "medium", "high"}:
            risk_norm = "medium"

        return self._clarification_questions(
            ambiguous_request=request,
            available_context=self._resolve_tree_context(
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
                available_context=available_context,
            ),
            operation_risk=risk_norm,
        )
