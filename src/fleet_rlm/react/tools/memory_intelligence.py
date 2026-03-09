"""Memory intelligence tools for sandbox-based agent operations."""

from __future__ import annotations

from contextlib import nullcontext
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import dspy

from ..tool_delegation import _sync_compatible_tool_callable
from .sandbox_helpers import _resolve_volume_path

if TYPE_CHECKING:
    from ..agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _MemoryIntelligenceContext:
    """Shared context for memory intelligence tool operations."""

    agent: "RLMReActChatAgent"


def _coerce_int(
    value: Any,
    *,
    default: int = 0,
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


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _prediction_value(prediction: Any, field_name: str, default: Any) -> Any:
    if isinstance(prediction, dict):
        return prediction.get(field_name, default)
    return getattr(prediction, field_name, default)


def _claim_delegate_slot_or_error(agent: "RLMReActChatAgent") -> dict[str, Any] | None:
    if agent._current_depth >= agent._max_depth:
        return {
            "status": "error",
            "error": (
                f"Max recursion depth ({agent._max_depth}) reached. "
                "Cannot run delegate operation."
            ),
        }

    claim_slot = getattr(agent, "_claim_delegate_slot", None)
    if not callable(claim_slot):
        return None

    claim_result = claim_slot()
    if (
        isinstance(claim_result, tuple)
        and len(claim_result) == 2
        and isinstance(claim_result[0], bool)
    ):
        allowed = bool(claim_result[0])
        limit = _coerce_int(claim_result[1], default=1, minimum=1)
        if not allowed:
            return {
                "status": "error",
                "error": (
                    "Delegate call budget reached for this turn. "
                    f"Maximum delegate calls per turn is {limit}."
                ),
                "delegate_max_calls_per_turn": limit,
            }

    return None


def _run_runtime_module(
    ctx: _MemoryIntelligenceContext,
    module_name: str,
    **kwargs: Any,
) -> tuple[Any | None, dict[str, Any] | None, bool]:
    guard_error = _claim_delegate_slot_or_error(ctx.agent)
    if guard_error is not None:
        return None, guard_error, False

    ctx.agent.start()

    try:
        module = ctx.agent.get_runtime_module(module_name)
    except Exception as exc:
        return (
            None,
            {
                "status": "error",
                "error": (
                    f"Failed to load runtime module '{module_name}': "
                    f"{type(exc).__name__}: {exc}"
                ),
            },
            False,
        )

    delegate_lm = getattr(ctx.agent, "delegate_lm", None)
    parent_lm = getattr(dspy.settings, "lm", None)

    fallback_used = False
    if delegate_lm is None:
        fallback_used = True
        record_fallback = getattr(ctx.agent, "_record_delegate_fallback", None)
        if callable(record_fallback):
            record_fallback()

    try:
        if delegate_lm is not None:
            lm_context = dspy.context(lm=delegate_lm)
        elif parent_lm is not None:
            lm_context = dspy.context(lm=parent_lm)
        else:
            lm_context = nullcontext()

        with lm_context:
            prediction = module(**kwargs)
    except Exception as exc:
        if delegate_lm is not None and parent_lm is not None:
            record_fallback = getattr(ctx.agent, "_record_delegate_fallback", None)
            if callable(record_fallback):
                record_fallback()
            fallback_used = True
            with dspy.context(lm=parent_lm):
                prediction = module(**kwargs)
        else:
            return (
                None,
                {
                    "status": "error",
                    "error": (
                        f"Runtime module '{module_name}' failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                },
                fallback_used,
            )

    return prediction, None, fallback_used


def _runtime_metadata(
    ctx: _MemoryIntelligenceContext,
    prediction: Any,
    *,
    fallback_used: bool,
) -> dict[str, Any]:
    return {
        "depth": _coerce_int(
            _prediction_value(prediction, "depth", ctx.agent._current_depth + 1),
            default=ctx.agent._current_depth + 1,
            minimum=0,
        ),
        "sub_agent_history": _coerce_int(
            _prediction_value(prediction, "sub_agent_history", 0),
            default=0,
            minimum=0,
        ),
        "delegate_lm_fallback": bool(fallback_used),
    }


async def _reload_volume_best_effort(
    ctx: _MemoryIntelligenceContext, *, reason: str
) -> None:
    if ctx.agent.interpreter._volume:
        try:
            await ctx.agent.interpreter.areload()
        except Exception as exc:
            logger.warning(
                "Failed to reload interpreter volume for %s: %s", reason, exc
            )


def _resolve_memory_root(root_path: str) -> tuple[str | None, dict[str, Any] | None]:
    try:
        return (
            _resolve_volume_path(
                root_path,
                default_root="/data/memory",
                allowed_root="/data",
            ),
            None,
        )
    except ValueError as exc:
        return None, {"status": "error", "error": str(exc)}


def _normalize_tree_nodes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    nodes: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized = {
            "path": str(item.get("path", "")),
            "type": str(item.get("type", "")),
            "size_bytes": _coerce_int(item.get("size_bytes", 0), minimum=0),
            "depth": _coerce_int(item.get("depth", 0), minimum=0),
        }
        nodes.append(normalized)
    return nodes


def build_memory_intelligence_tools(agent: "RLMReActChatAgent") -> list[Any]:
    """Build memory intelligence tools bound to a shared context object."""
    from dspy import Tool

    ctx = _MemoryIntelligenceContext(agent=agent)

    async def memory_tree(
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        """Return a bounded tree-style snapshot of the Modal volume memory path."""
        resolved_path, error = _resolve_memory_root(root_path)
        if error is not None:
            return error

        await _reload_volume_best_effort(ctx, reason="memory_tree")

        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx,
            "memory_tree",
            root_path=resolved_path,
            max_depth=_coerce_int(max_depth, default=4, minimum=0, maximum=12),
            include_hidden=bool(include_hidden),
        )
        if runtime_error is not None:
            return runtime_error

        nodes = _normalize_tree_nodes(_prediction_value(prediction, "nodes", []))

        return {
            "status": "ok",
            "root_path": resolved_path,
            "nodes": nodes,
            "total_files": _coerce_int(
                _prediction_value(
                    prediction,
                    "total_files",
                    len([n for n in nodes if n["type"] == "file"]),
                ),
                minimum=0,
            ),
            "total_dirs": _coerce_int(
                _prediction_value(
                    prediction,
                    "total_dirs",
                    len([n for n in nodes if n["type"] == "dir"]),
                ),
                minimum=0,
            ),
            "truncated": bool(_prediction_value(prediction, "truncated", False)),
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
        }

    async def memory_action_intent(
        user_request: str,
        policy_constraints: str = "",
    ) -> dict[str, Any]:
        """Classify intended memory action and risk; this tool never mutates state."""
        tree_payload = await memory_tree()
        if tree_payload.get("status") != "ok":
            return tree_payload

        constraints_text = (
            policy_constraints
            or "Prefer non-destructive operations and ask for confirmation on risky actions."
        )

        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx,
            "memory_action_intent",
            user_request=user_request,
            current_tree=list(tree_payload.get("nodes", []) or []),
            policy_constraints=constraints_text,
        )
        if runtime_error is not None:
            return runtime_error

        risk_level = (
            str(_prediction_value(prediction, "risk_level", "medium")).strip().lower()
        )
        if risk_level not in {"low", "medium", "high"}:
            risk_level = "medium"

        return {
            "status": "ok",
            "action_type": str(_prediction_value(prediction, "action_type", "noop")),
            "target_paths": _coerce_str_list(
                _prediction_value(prediction, "target_paths", [])
            ),
            "content_plan": _coerce_str_list(
                _prediction_value(prediction, "content_plan", [])
            ),
            "risk_level": risk_level,
            "requires_confirmation": bool(
                _prediction_value(prediction, "requires_confirmation", True)
            ),
            "rationale": str(_prediction_value(prediction, "rationale", "")),
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
        }

    async def memory_structure_audit(
        usage_goals: str = "",
    ) -> dict[str, Any]:
        """Audit memory structure and return organization recommendations."""
        tree_payload = await memory_tree()
        if tree_payload.get("status") != "ok":
            return tree_payload

        goals = (
            usage_goals or "Keep memory discoverable, consistent, and easy to maintain."
        )

        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx,
            "memory_structure_audit",
            tree_snapshot=list(tree_payload.get("nodes", []) or []),
            usage_goals=goals,
        )
        if runtime_error is not None:
            return runtime_error

        return {
            "status": "ok",
            "issues": _coerce_str_list(_prediction_value(prediction, "issues", [])),
            "recommended_layout": _coerce_str_list(
                _prediction_value(prediction, "recommended_layout", [])
            ),
            "naming_conventions": _coerce_str_list(
                _prediction_value(prediction, "naming_conventions", [])
            ),
            "retention_rules": _coerce_str_list(
                _prediction_value(prediction, "retention_rules", [])
            ),
            "priority_fixes": _coerce_str_list(
                _prediction_value(prediction, "priority_fixes", [])
            ),
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
        }

    async def memory_structure_migration_plan(
        approved_constraints: str = "",
    ) -> dict[str, Any]:
        """Generate a reversible migration plan from current memory audit findings."""
        audit = await memory_structure_audit()
        if audit.get("status") != "ok":
            return audit

        constraints = (
            approved_constraints
            or "No destructive operation without explicit confirmation and rollback."
        )

        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx,
            "memory_structure_migration_plan",
            audit_findings=list(audit.get("issues", []) or []),
            approved_constraints=constraints,
        )
        if runtime_error is not None:
            return runtime_error

        raw_operations = _prediction_value(prediction, "operations", [])
        operations: list[dict[str, str]] = []
        if isinstance(raw_operations, list):
            for item in raw_operations:
                if isinstance(item, dict):
                    operations.append({str(k): str(v) for k, v in item.items()})

        estimated_risk = (
            str(_prediction_value(prediction, "estimated_risk", "medium"))
            .strip()
            .lower()
        )
        if estimated_risk not in {"low", "medium", "high"}:
            estimated_risk = "medium"

        return {
            "status": "ok",
            "operations": operations,
            "rollback_steps": _coerce_str_list(
                _prediction_value(prediction, "rollback_steps", [])
            ),
            "verification_checks": _coerce_str_list(
                _prediction_value(prediction, "verification_checks", [])
            ),
            "estimated_risk": estimated_risk,
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
        }

    async def clarification_questions(
        request: str,
        operation_risk: str = "medium",
    ) -> dict[str, Any]:
        """Generate clarification questions for ambiguous/high-risk operations."""
        tree_payload = await memory_tree()
        if tree_payload.get("status") != "ok":
            return tree_payload

        tree_nodes = list(tree_payload.get("nodes", []) or [])[:20]
        available_context = (
            f"memory_root=/data/memory; nodes_sample={tree_nodes}; "
            f"history_turns={ctx.agent.history_turns()}"
        )

        risk_norm = operation_risk.strip().lower()
        if risk_norm not in {"low", "medium", "high"}:
            risk_norm = "medium"

        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx,
            "clarification_questions",
            ambiguous_request=request,
            available_context=available_context,
            operation_risk=risk_norm,
        )
        if runtime_error is not None:
            return runtime_error

        proceed_without_answer = bool(
            _prediction_value(prediction, "proceed_without_answer", False)
        )
        if risk_norm == "high":
            proceed_without_answer = False

        return {
            "status": "ok",
            "questions": _coerce_str_list(
                _prediction_value(prediction, "questions", [])
            ),
            "blocking_unknowns": _coerce_str_list(
                _prediction_value(prediction, "blocking_unknowns", [])
            ),
            "safe_default": str(_prediction_value(prediction, "safe_default", "")),
            "proceed_without_answer": proceed_without_answer,
            **_runtime_metadata(ctx, prediction, fallback_used=fallback_used),
        }

    return [
        Tool(
            _sync_compatible_tool_callable(memory_tree),
            name="memory_tree",
            desc="Return a bounded file-tree snapshot for a path in Modal volume memory",
        ),
        Tool(
            _sync_compatible_tool_callable(memory_action_intent),
            name="memory_action_intent",
            desc="Infer memory action intent, risk, and confirmation needs from a request",
        ),
        Tool(
            _sync_compatible_tool_callable(memory_structure_audit),
            name="memory_structure_audit",
            desc="Audit memory layout and recommend structure conventions",
        ),
        Tool(
            _sync_compatible_tool_callable(memory_structure_migration_plan),
            name="memory_structure_migration_plan",
            desc="Generate reversible migration operations from memory audit findings",
        ),
        Tool(
            _sync_compatible_tool_callable(clarification_questions),
            name="clarification_questions",
            desc="Generate clarification questions for ambiguous or risky memory operations",
        ),
    ]
