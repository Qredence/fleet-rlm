"""Memory intelligence tools for sandbox-based agent operations.

These tools provide memory tree inspection, action intent classification,
structure auditing, and clarification question generation.  They delegate
to recursive sub-agents (true recursion) instead of single-shot RLM modules.

Extracted from tools_sandbox.py as part of the modularization effort
(Linear: QRE-273).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .delegate_sub_agent import parse_json_from_response, spawn_delegate_sub_agent
from .tools_sandbox_helpers import _resolve_volume_path

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


def build_memory_intelligence_tools(agent: "RLMReActChatAgent") -> list[Any]:
    """Build memory intelligence tools bound to *agent*.

    These tools provide intelligent analysis of the Modal Volume memory
    structure, including:
    - Tree-style file system snapshots
    - Action intent classification for memory operations
    - Structure auditing and recommendations
    - Migration planning
    - Clarification question generation

    Returns a list of ``dspy.Tool`` wrappers ready to be appended to the
    main tool list built by ``build_tool_list``.
    """

    def memory_tree(
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        """Return a bounded tree-style snapshot of the Modal volume memory path."""
        try:
            resolved_path = _resolve_volume_path(
                root_path,
                default_root="/data/memory",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        if agent.interpreter._volume:
            try:
                agent.interpreter.reload()
            except Exception as exc:
                logger.warning(
                    "Failed to reload interpreter volume for memory_tree: %s", exc
                )

        prompt = (
            "List the file tree of the Modal volume.\n\n"
            f"Root path: {resolved_path}\n"
            f"Max depth: {max(0, int(max_depth))}\n"
            f"Include hidden: {bool(include_hidden)}\n\n"
            "Provide your response with:\n"
            "- nodes: list of dicts with keys path, type, size_bytes, depth\n"
            "- total_files: integer count of files\n"
            "- total_dirs: integer count of directories\n"
            "- truncated: boolean whether the listing was truncated"
        )

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        structured = parse_json_from_response(response_text) or {}

        nodes = structured.get("nodes", [])
        if not isinstance(nodes, list):
            nodes = []

        total_files_raw = structured.get("total_files", 0)
        try:
            total_files = int(total_files_raw)
        except (TypeError, ValueError):
            total_files = 0
        total_files = max(0, total_files)

        total_dirs_raw = structured.get("total_dirs", 0)
        try:
            total_dirs = int(total_dirs_raw)
        except (TypeError, ValueError):
            total_dirs = 0
        total_dirs = max(0, total_dirs)

        truncated = bool(structured.get("truncated", False))

        return {
            "status": "ok",
            "root_path": resolved_path,
            "nodes": nodes,
            "total_files": total_files,
            "total_dirs": total_dirs,
            "truncated": truncated,
            "depth": result.get("depth", agent._current_depth + 1),
            "sub_agent_history": result.get("sub_agent_history", 0),
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

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

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
            "depth": result.get("depth", agent._current_depth + 1),
            "sub_agent_history": result.get("sub_agent_history", 0),
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

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

        if result.get("status") == "error":
            return result

        return {
            "status": "ok",
            "issues": [],
            "recommended_layout": [],
            "naming_conventions": [],
            "retention_rules": [],
            "priority_fixes": [],
            "depth": result.get("depth", agent._current_depth + 1),
            "sub_agent_history": result.get("sub_agent_history", 0),
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

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

        if result.get("status") == "error":
            return result

        return {
            "status": "ok",
            "operations": [],
            "rollback_steps": [],
            "verification_checks": [],
            "estimated_risk": "medium",
            "depth": result.get("depth", agent._current_depth + 1),
            "sub_agent_history": result.get("sub_agent_history", 0),
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
            f"history_turns={agent.history_turns()}"
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

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

        if result.get("status") == "error":
            return result

        proceed_without_answer = False
        if risk_norm == "high":
            proceed_without_answer = False

        return {
            "status": "ok",
            "questions": [],
            "blocking_unknowns": [],
            "safe_default": "",
            "proceed_without_answer": proceed_without_answer,
            "depth": result.get("depth", agent._current_depth + 1),
            "sub_agent_history": result.get("sub_agent_history", 0),
        }

    # -- Assemble tool list --------------------------------------------------

    from dspy import Tool

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
