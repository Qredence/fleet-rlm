"""Memory-intelligence tool builders for sandbox runtimes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from fleet_rlm.runtime.agent.signatures import MemoryMigrationOperation, VolumeTreeNode
from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable
from fleet_rlm.runtime.execution.interpreter_protocol import RLMInterpreterProtocol
from fleet_rlm.runtime.execution.storage_paths import runtime_storage_roots

from .llm_tools import coerce_int as _coerce_int
from .llm_tools import prediction_value as _prediction_value
from .llm_tools import run_cached_runtime_module as _run_runtime_module
from .llm_tools import runtime_metadata as _runtime_metadata
from .sandbox_common import _resolve_path_or_error, _SandboxToolContext

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


logger = logging.getLogger(__name__)


async def _reload_memory_volume_best_effort(
    ctx: _SandboxToolContext,
    *,
    reason: str,
) -> None:
    interpreter = getattr(ctx.agent, "interpreter", None)
    if not getattr(interpreter, "_volume", None):
        return
    reload_fn = getattr(interpreter, "reload", None)
    if callable(reload_fn):
        try:
            reload_fn()
        except Exception:
            logger.warning(
                "Best-effort volume reload failed during %s",
                reason,
                exc_info=True,
            )


def _resolve_memory_root(
    agent: RLMReActChatAgent,
    root_path: str,
) -> tuple[str | None, dict[str, Any] | None]:
    roots = runtime_storage_roots(cast(RLMInterpreterProtocol, agent.interpreter))
    if root_path.strip() == "/data/memory" and roots.memory_root != "/data/memory":
        root_path = roots.memory_root
    return _resolve_path_or_error(
        path=root_path,
        default_root=roots.memory_root,
        allowed_root=roots.allowed_root,
    )


def _normalize_tree_nodes(value: Any) -> list[VolumeTreeNode]:
    if not isinstance(value, list):
        return []

    nodes: list[VolumeTreeNode] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        node: VolumeTreeNode = {
            "path": str(item.get("path", "")).strip(),
            "type": str(item.get("type", "")).strip() or "file",
            "size_bytes": _coerce_int(item.get("size_bytes", 0), minimum=0),
            "depth": _coerce_int(item.get("depth", 0), minimum=0),
        }
        if not node["path"]:
            continue
        if node["type"] not in {"file", "dir"}:
            node["type"] = "file"
        nodes.append(node)
    return nodes


def _normalize_memory_migration_operations(
    value: Any,
) -> list[MemoryMigrationOperation]:
    if not isinstance(value, list):
        return []

    operations: list[MemoryMigrationOperation] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        op_type = str(item.get("op", item.get("type", ""))).strip().lower()
        if op_type not in {"move", "rename", "merge", "delete", "mkdir", "copy"}:
            continue

        operation: MemoryMigrationOperation = {
            "op": op_type,
            "src": str(item.get("src", item.get("source", ""))).strip(),
            "dst": str(item.get("dst", item.get("destination", ""))).strip(),
            "reason": str(item.get("reason", "")).strip(),
        }
        if any(operation.values()):
            operations.append(operation)
    return operations


def _normalize_non_empty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def build_memory_intelligence_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build memory-analysis tools backed by cached runtime modules."""
    from dspy import Tool

    ctx = _SandboxToolContext(agent=agent)

    async def memory_tree(
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        resolved_path, error = _resolve_memory_root(ctx.agent, root_path)
        if error is not None:
            return error

        await _reload_memory_volume_best_effort(ctx, reason="memory_tree")
        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
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
                    len([node for node in nodes if node["type"] == "file"]),
                ),
                minimum=0,
            ),
            "total_dirs": _coerce_int(
                _prediction_value(
                    prediction,
                    "total_dirs",
                    len([node for node in nodes if node["type"] == "dir"]),
                ),
                minimum=0,
            ),
            "truncated": bool(_prediction_value(prediction, "truncated", False)),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    async def memory_action_intent(
        user_request: str,
        policy_constraints: str = "",
    ) -> dict[str, Any]:
        constraints_text = (
            policy_constraints
            or "Prefer non-destructive operations and ask for confirmation on risky actions."
        )
        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "memory_action_intent",
            user_request=user_request,
            policy_constraints=constraints_text,
        )
        if runtime_error is not None:
            return runtime_error

        risk_level = (
            str(_prediction_value(prediction, "risk_level", "medium")).strip().lower()
        )
        if risk_level not in {"low", "medium", "high"}:
            risk_level = "medium"

        action_type = (
            str(_prediction_value(prediction, "action_type", "noop")).strip().lower()
        )
        if action_type not in {
            "read",
            "write",
            "append",
            "move",
            "delete",
            "mkdir",
            "tree",
            "audit",
            "migrate",
            "noop",
        }:
            action_type = "noop"

        return {
            "status": "ok",
            "action_type": action_type,
            "target_paths": _normalize_non_empty_strings(
                _prediction_value(prediction, "target_paths", [])
            ),
            "content_plan": _normalize_non_empty_strings(
                _prediction_value(prediction, "content_plan", [])
            ),
            "risk_level": risk_level,
            "requires_confirmation": bool(
                _prediction_value(prediction, "requires_confirmation", True)
            ),
            "rationale": str(_prediction_value(prediction, "rationale", "")).strip(),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    async def memory_structure_audit(usage_goals: str = "") -> dict[str, Any]:
        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "memory_structure_audit",
            usage_goals=(
                usage_goals
                or "Keep memory discoverable, consistent, and easy to maintain."
            ),
        )
        if runtime_error is not None:
            return runtime_error

        return {
            "status": "ok",
            "issues": _normalize_non_empty_strings(
                _prediction_value(prediction, "issues", [])
            ),
            "recommended_layout": _normalize_non_empty_strings(
                _prediction_value(prediction, "recommended_layout", [])
            ),
            "naming_conventions": _normalize_non_empty_strings(
                _prediction_value(prediction, "naming_conventions", [])
            ),
            "retention_rules": _normalize_non_empty_strings(
                _prediction_value(prediction, "retention_rules", [])
            ),
            "priority_fixes": _normalize_non_empty_strings(
                _prediction_value(prediction, "priority_fixes", [])
            ),
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    async def memory_structure_migration_plan(
        approved_constraints: str = "",
    ) -> dict[str, Any]:
        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "memory_structure_migration_plan",
            approved_constraints=(
                approved_constraints
                or "No destructive operation without explicit confirmation and rollback."
            ),
        )
        if runtime_error is not None:
            return runtime_error

        operations = _normalize_memory_migration_operations(
            _prediction_value(prediction, "operations", [])
        )

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
            "rollback_steps": _normalize_non_empty_strings(
                _prediction_value(prediction, "rollback_steps", [])
            ),
            "verification_checks": _normalize_non_empty_strings(
                _prediction_value(prediction, "verification_checks", [])
            ),
            "estimated_risk": estimated_risk,
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    async def clarification_questions(
        request: str,
        operation_risk: str = "medium",
    ) -> dict[str, Any]:
        risk_norm = operation_risk.strip().lower()
        if risk_norm not in {"low", "medium", "high"}:
            risk_norm = "medium"

        prediction, runtime_error, fallback_used = _run_runtime_module(
            ctx.agent,
            "clarification_questions",
            request=request,
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
            "questions": _normalize_non_empty_strings(
                _prediction_value(prediction, "questions", [])
            ),
            "blocking_unknowns": _normalize_non_empty_strings(
                _prediction_value(prediction, "blocking_unknowns", [])
            ),
            "safe_default": str(
                _prediction_value(prediction, "safe_default", "")
            ).strip(),
            "proceed_without_answer": proceed_without_answer,
            **_runtime_metadata(ctx.agent, prediction, fallback_used=fallback_used),
        }

    return [
        Tool(
            _sync_compatible_tool_callable(memory_tree),
            name="memory_tree",
            desc="Return a bounded file-tree snapshot for a path in durable volume memory",
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
