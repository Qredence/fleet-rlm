"""Memory intelligence tools for sandbox-based agent operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..delegate_sub_agent import parse_json_from_response, spawn_delegate_sub_agent
from .sandbox_helpers import _resolve_volume_path

if TYPE_CHECKING:
    from ..agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _MemoryIntelligenceContext:
    """Shared context for memory intelligence tool operations."""

    agent: "RLMReActChatAgent"


def _safe_depth(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _default_depth(ctx: _MemoryIntelligenceContext, result: dict[str, Any]) -> int:
    return int(result.get("depth", ctx.agent._current_depth + 1))


def _default_history(ctx: _MemoryIntelligenceContext, result: dict[str, Any]) -> int:
    return int(result.get("sub_agent_history", 0))


def _reload_volume_best_effort(ctx: _MemoryIntelligenceContext, *, reason: str) -> None:
    if ctx.agent.interpreter._volume:
        try:
            ctx.agent.interpreter.reload()
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


def build_memory_intelligence_tools(agent: "RLMReActChatAgent") -> list[Any]:
    """Build memory intelligence tools bound to a shared context object."""
    from dspy import Tool

    ctx = _MemoryIntelligenceContext(agent=agent)

    def memory_tree(
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        """Return a bounded tree-style snapshot of the Modal volume memory path."""
        resolved_path, error = _resolve_memory_root(root_path)
        if error is not None:
            return error

        _reload_volume_best_effort(ctx, reason="memory_tree")

        prompt = (
            "List the file tree of the Modal volume.\n\n"
            f"Root path: {resolved_path}\n"
            f"Max depth: {_safe_depth(max_depth)}\n"
            f"Include hidden: {bool(include_hidden)}\n\n"
            "Provide your response with:\n"
            "- nodes: list of dicts with keys path, type, size_bytes, depth\n"
            "- total_files: integer count of files\n"
            "- total_dirs: integer count of directories\n"
            "- truncated: boolean whether the listing was truncated"
        )

        result = spawn_delegate_sub_agent(ctx.agent, prompt=prompt)
        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        structured = parse_json_from_response(response_text) or {}
        nodes = structured.get("nodes", [])
        if not isinstance(nodes, list):
            nodes = []
        total_files_raw = structured.get("total_files", 0)
        try:
            total_files = max(0, int(total_files_raw))
        except (TypeError, ValueError):
            total_files = 0
        total_dirs_raw = structured.get("total_dirs", 0)
        try:
            total_dirs = max(0, int(total_dirs_raw))
        except (TypeError, ValueError):
            total_dirs = 0
        truncated = bool(structured.get("truncated", False))

        return {
            "status": "ok",
            "root_path": resolved_path,
            "nodes": nodes,
            "total_files": total_files,
            "total_dirs": total_dirs,
            "truncated": truncated,
            "depth": _default_depth(ctx, result),
            "sub_agent_history": _default_history(ctx, result),
        }

    def memory_action_intent(
        user_request: str,
        policy_constraints: str = "",
    ) -> dict[str, Any]:
        """Classify intended memory action and risk; this tool never mutates state."""
        tree_payload = memory_tree()
        current_tree = list(tree_payload.get("nodes", []) or [])
        constraints_text = (
            policy_constraints
            or "Prefer non-destructive operations and ask for confirmation on risky actions."
        )

        prompt = (
            "Classify the intended memory action from the user request.\n\n"
            f"User request: {user_request}\n"
            f"Current tree: {current_tree}\n"
            f"Policy constraints: {constraints_text}\n\n"
            "Provide your response with:\n"
            "- action_type: one of read, write, append, move, delete, mkdir, tree, audit, migrate, noop\n"
            "- target_paths: list of target paths\n"
            "- content_plan: list of content operations\n"
            "- risk_level: one of low, medium, high\n"
            "- requires_confirmation: boolean\n"
            "- rationale: explanation"
        )

        result = spawn_delegate_sub_agent(ctx.agent, prompt=prompt)
        if result.get("status") == "error":
            return result

        return {
            "status": "ok",
            "action_type": "noop",
            "target_paths": [],
            "content_plan": [],
            "risk_level": "medium",
            "requires_confirmation": True,
            "rationale": result.get("assistant_response", ""),
            "depth": _default_depth(ctx, result),
            "sub_agent_history": _default_history(ctx, result),
        }

    def memory_structure_audit(
        usage_goals: str = "",
    ) -> dict[str, Any]:
        """Audit memory structure and return organization recommendations."""
        tree_payload = memory_tree()
        if tree_payload.get("status") != "ok":
            return tree_payload
        tree_snapshot = list(tree_payload.get("nodes", []) or [])
        goals = (
            usage_goals or "Keep memory discoverable, consistent, and easy to maintain."
        )

        prompt = (
            "Audit the memory structure and recommend improvements.\n\n"
            f"Tree snapshot: {tree_snapshot}\n"
            f"Usage goals: {goals}\n\n"
            "Provide your response with:\n"
            "- issues: list of identified issues\n"
            "- recommended_layout: list of recommended paths\n"
            "- naming_conventions: list of naming rules\n"
            "- retention_rules: list of retention policies\n"
            "- priority_fixes: list of highest-priority fixes"
        )

        result = spawn_delegate_sub_agent(ctx.agent, prompt=prompt)
        if result.get("status") == "error":
            return result

        return {
            "status": "ok",
            "issues": [],
            "recommended_layout": [],
            "naming_conventions": [],
            "retention_rules": [],
            "priority_fixes": [],
            "depth": _default_depth(ctx, result),
            "sub_agent_history": _default_history(ctx, result),
        }

    def memory_structure_migration_plan(
        approved_constraints: str = "",
    ) -> dict[str, Any]:
        """Generate a reversible migration plan from current memory audit findings."""
        audit = memory_structure_audit()
        if audit.get("status") != "ok":
            return audit
        findings = list(audit.get("issues", []) or [])
        constraints = (
            approved_constraints
            or "No destructive operation without explicit confirmation and rollback."
        )

        prompt = (
            "Generate a reversible migration plan for the memory structure.\n\n"
            f"Audit findings: {findings}\n"
            f"Approved constraints: {constraints}\n\n"
            "Provide your response with:\n"
            "- operations: list of dicts with keys op, src, dst, reason\n"
            "- rollback_steps: list of rollback instructions\n"
            "- verification_checks: list of verification commands\n"
            "- estimated_risk: one of low, medium, high"
        )

        result = spawn_delegate_sub_agent(ctx.agent, prompt=prompt)
        if result.get("status") == "error":
            return result

        return {
            "status": "ok",
            "operations": [],
            "rollback_steps": [],
            "verification_checks": [],
            "estimated_risk": "medium",
            "depth": _default_depth(ctx, result),
            "sub_agent_history": _default_history(ctx, result),
        }

    def clarification_questions(
        request: str,
        operation_risk: str = "medium",
    ) -> dict[str, Any]:
        """Generate clarification questions for ambiguous/high-risk operations."""
        tree_payload = memory_tree()
        tree_nodes = list(tree_payload.get("nodes", []) or [])[:20]
        available_context = (
            f"memory_root=/data/memory; nodes_sample={tree_nodes}; "
            f"history_turns={ctx.agent.history_turns()}"
        )
        risk_norm = operation_risk.strip().lower()
        if risk_norm not in {"low", "medium", "high"}:
            risk_norm = "medium"

        prompt = (
            "Generate clarification questions for an ambiguous or risky operation.\n\n"
            f"Request: {request}\n"
            f"Available context: {available_context}\n"
            f"Operation risk: {risk_norm}\n\n"
            "Provide your response with:\n"
            "- questions: list of clarification questions\n"
            "- blocking_unknowns: list of unknowns that block execution\n"
            "- safe_default: a safe default action\n"
            "- proceed_without_answer: boolean (always false for high risk)"
        )

        result = spawn_delegate_sub_agent(ctx.agent, prompt=prompt)
        if result.get("status") == "error":
            return result

        proceed_without_answer = False if risk_norm == "high" else False
        return {
            "status": "ok",
            "questions": [],
            "blocking_unknowns": [],
            "safe_default": "",
            "proceed_without_answer": proceed_without_answer,
            "depth": _default_depth(ctx, result),
            "sub_agent_history": _default_history(ctx, result),
        }

    return [
        Tool(
            memory_tree,
            name="memory_tree",
            desc="Return a bounded file-tree snapshot for a path in Modal volume memory",
        ),
        Tool(
            memory_action_intent,
            name="memory_action_intent",
            desc="Infer memory action intent, risk, and confirmation needs from a request",
        ),
        Tool(
            memory_structure_audit,
            name="memory_structure_audit",
            desc="Audit memory layout and recommend structure conventions",
        ),
        Tool(
            memory_structure_migration_plan,
            name="memory_structure_migration_plan",
            desc="Generate reversible migration operations from memory audit findings",
        ),
        Tool(
            clarification_questions,
            name="clarification_questions",
            desc="Generate clarification questions for ambiguous or risky memory operations",
        ),
    ]
