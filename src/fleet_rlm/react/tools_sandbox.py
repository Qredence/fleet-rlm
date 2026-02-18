"""Sandbox, RLM-delegate, and buffer/volume tool definitions.

These tools run code inside the Modal sandbox or delegate to RLM
sub-agents.  They are built by :func:`build_sandbox_tools` and merged
into the main tool list by :func:`~fleet_rlm.react.tools.build_tool_list`.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from ..core.interpreter import ExecutionProfile
from .tools import (
    chunk_text,
    chunk_to_text,
    execute_submit,
    resolve_document,
    _rlm_trajectory_payload,
)

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    """Coerce common model output variants into booleans safely."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _coerce_int(
    value: Any,
    *,
    default: int = 0,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    """Coerce model outputs into bounded ints with a safe fallback."""
    if value is None:
        parsed = default
    else:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default

    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_sandbox_tools(
    agent: "RLMReActChatAgent",
) -> list[Any]:
    """Build sandbox / RLM / buffer / volume tools bound to *agent*.

    Returns a list of ``dspy.Tool`` wrappers ready to be appended to the
    main tool list built by ``build_tool_list``.
    """

    # -- Long-context analysis -----------------------------------------------

    def parallel_semantic_map(
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        buffer_name: str = "findings",
    ) -> dict[str, Any]:
        """Run parallel semantic analysis over chunks via llm_query_batched."""
        text = resolve_document(agent, "active")
        chunks = chunk_text(
            text, chunk_strategy, size=80_000, overlap=1_000, pattern=""
        )
        chunk_texts = [chunk_to_text(c) for c in chunks][:max_chunks]

        prompts = []
        for idx, chunk_item in enumerate(chunk_texts):
            prompts.append(
                (
                    f"Query: {query}\n"
                    f"Chunk index: {idx}\n"
                    "Return concise findings as plain text.\n\n"
                    f"{chunk_item[:6000]}"
                )
            )

        code = """
clear_buffer(buffer_name)
responses = llm_query_batched(prompts)
for idx, response in enumerate(responses):
    add_buffer(buffer_name, {"chunk_index": idx, "response": response})

SUBMIT(
    status="ok",
    strategy=chunk_strategy,
    chunk_count=len(prompts),
    findings_count=len(responses),
    buffer_name=buffer_name,
)
"""
        return execute_submit(
            agent,
            code,
            variables={
                "prompts": prompts,
                "buffer_name": buffer_name,
                "chunk_strategy": chunk_strategy,
            },
        )

    def analyze_long_document(
        query: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Analyze a long document with the AnalyzeLongDocument RLM signature."""
        agent.start()
        document = resolve_document(agent, alias)
        rlm = agent.get_runtime_module("analyze_long_document")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(document=document, query=query)
        response = {
            "status": "ok",
            "findings": result.findings,
            "answer": result.answer,
            "sections_examined": result.sections_examined,
            "doc_chars": len(document),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def summarize_long_document(
        focus: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Summarize a long document with the SummarizeLongDocument RLM signature."""
        agent.start()
        document = resolve_document(agent, alias)
        rlm = agent.get_runtime_module("summarize_long_document")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(document=document, focus=focus)
        response = {
            "status": "ok",
            "summary": result.summary,
            "key_points": result.key_points,
            "coverage_pct": result.coverage_pct,
            "doc_chars": len(document),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def extract_from_logs(
        query: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Extract structured patterns from log text via ExtractFromLogs RLM signature."""
        agent.start()
        logs = resolve_document(agent, alias)
        rlm = agent.get_runtime_module("extract_from_logs")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(logs=logs, query=query)
        response = {
            "status": "ok",
            "matches": result.matches,
            "patterns": result.patterns,
            "time_range": result.time_range,
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def grounded_answer(
        query: str,
        alias: str = "active",
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Answer a query with explicit machine-readable citations."""
        try:
            max_chunks_int = int(max_chunks)
        except (TypeError, ValueError):
            return {
                "status": "error",
                "error": "Invalid max_chunks value. It must be a positive integer.",
            }
        if max_chunks_int <= 0:
            return {
                "status": "error",
                "error": "max_chunks must be a positive integer.",
            }

        agent.start()
        document = resolve_document(agent, alias)
        chunks = chunk_text(
            document, chunk_strategy, size=80_000, overlap=1_000, pattern=""
        )
        evidence_chunks = [chunk_to_text(chunk) for chunk in chunks][:max_chunks_int]
        if not evidence_chunks:
            return {
                "status": "error",
                "error": "No evidence chunks available. Load a non-empty document first.",
            }

        rlm = agent.get_runtime_module("grounded_answer")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(
                query=query,
                evidence_chunks=evidence_chunks,
                response_style="concise",
            )

        citations_raw = getattr(result, "citations", []) or []
        citations: list[dict[str, str]] = []
        for idx, item in enumerate(citations_raw):
            row = item if isinstance(item, dict) else {"evidence": str(item)}
            normalized = {
                "source": str(row.get("source", alias)),
                "chunk_id": str(row.get("chunk_id", idx)),
                "evidence": str(row.get("evidence", ""))[:280],
                "reason": str(row.get("reason", "")),
            }
            citations.append(normalized)

        response = {
            "status": "ok",
            "answer": str(getattr(result, "answer", "")),
            "citations": citations,
            "confidence": _coerce_int(
                getattr(result, "confidence", 0),
                default=0,
                min_value=0,
                max_value=100,
            ),
            "coverage_notes": str(getattr(result, "coverage_notes", "")),
            "doc_chars": len(document),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def triage_incident_logs(
        query: str,
        alias: str = "active",
        service_context: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Triage logs into severity, causes, impact, and actions."""
        agent.start()
        logs = resolve_document(agent, alias)
        rlm = agent.get_runtime_module("triage_incident_logs")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(logs=logs, service_context=service_context, query=query)

        severity = str(getattr(result, "severity", "")).strip().lower()
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "low"

        response = {
            "status": "ok",
            "severity": severity,
            "probable_root_causes": list(
                getattr(result, "probable_root_causes", []) or []
            ),
            "impacted_components": list(
                getattr(result, "impacted_components", []) or []
            ),
            "recommended_actions": list(
                getattr(result, "recommended_actions", []) or []
            ),
            "time_range": str(getattr(result, "time_range", "unknown")),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def plan_code_change(
        task: str,
        repo_context: str = "",
        constraints: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Build a structured code-change plan from task and constraints."""
        agent.start()
        context_text = repo_context or "No additional repo context provided."
        constraints_text = (
            constraints or "Keep changes minimal and backward compatible."
        )
        rlm = agent.get_runtime_module("plan_code_change")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(
                task=task,
                repo_context=context_text,
                constraints=constraints_text,
            )

        response = {
            "status": "ok",
            "plan_steps": list(getattr(result, "plan_steps", []) or []),
            "files_to_touch": list(getattr(result, "files_to_touch", []) or []),
            "validation_commands": list(
                getattr(result, "validation_commands", []) or []
            ),
            "risks": list(getattr(result, "risks", []) or []),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def propose_core_memory_update(
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Propose safe updates to core memory from current state/history."""
        agent.start()
        history_entries = agent.history_messages()
        turn_lines = []
        for idx, turn in enumerate(history_entries[-20:], start=1):
            if isinstance(turn, dict):
                user_msg = str(turn.get("user_request", ""))
                assistant_msg = str(turn.get("assistant_response", ""))
                turn_lines.append(
                    f"Turn {idx}\nUser: {user_msg}\nAssistant: {assistant_msg}"
                )
            else:
                turn_lines.append(f"Turn {idx}\n{turn}")
        turn_history = "\n\n".join(turn_lines) or "No recent turns."
        current_memory = agent.fmt_core_memory()

        rlm = agent.get_runtime_module("propose_core_memory_update")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(turn_history=turn_history, current_memory=current_memory)

        response = {
            "status": "ok",
            "keep": list(getattr(result, "keep", []) or []),
            "update": list(getattr(result, "update", []) or []),
            "remove": list(getattr(result, "remove", []) or []),
            "rationale": str(getattr(result, "rationale", "")),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

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
                # Reload failures are non-fatal for memory_tree; log and continue.
                logger.warning(
                    "Failed to reload interpreter volume for memory_tree: %s", exc
                )

        rlm = agent.get_runtime_module("memory_tree")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(
                root_path=resolved_path,
                max_depth=max(0, int(max_depth)),
                include_hidden=bool(include_hidden),
            )

        raw_nodes = list(getattr(result, "nodes", []) or [])
        node_cap = 2000
        normalized_nodes: list[dict[str, str]] = []
        for idx, raw in enumerate(raw_nodes[:node_cap]):
            row = raw if isinstance(raw, dict) else {"path": str(raw)}
            normalized_nodes.append(
                {
                    "path": str(row.get("path", "")),
                    "type": str(row.get("type", "unknown")),
                    "size_bytes": str(row.get("size_bytes", "0")),
                    "depth": str(row.get("depth", idx)),
                }
            )

        return {
            "status": "ok",
            "root_path": resolved_path,
            "nodes": normalized_nodes,
            "total_files": int(getattr(result, "total_files", 0) or 0),
            "total_dirs": int(getattr(result, "total_dirs", 0) or 0),
            "truncated": bool(getattr(result, "truncated", False))
            or len(raw_nodes) > node_cap,
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
        rlm = agent.get_runtime_module("memory_action_intent")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(
                user_request=user_request,
                current_tree=current_tree,
                policy_constraints=constraints_text,
            )

        action_type = str(getattr(result, "action_type", "noop")).strip().lower()
        allowed_actions = {
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
        }
        if action_type not in allowed_actions:
            action_type = "noop"

        risk_level = str(getattr(result, "risk_level", "medium")).strip().lower()
        if risk_level not in {"low", "medium", "high"}:
            risk_level = "medium"

        requires_confirmation = _coerce_bool(
            getattr(result, "requires_confirmation", risk_level == "high"),
            default=risk_level == "high",
        )

        return {
            "status": "ok",
            "action_type": action_type,
            "target_paths": list(getattr(result, "target_paths", []) or []),
            "content_plan": list(getattr(result, "content_plan", []) or []),
            "risk_level": risk_level,
            "requires_confirmation": requires_confirmation,
            "rationale": str(getattr(result, "rationale", "")),
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
        rlm = agent.get_runtime_module("memory_structure_audit")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(tree_snapshot=tree_snapshot, usage_goals=goals)

        return {
            "status": "ok",
            "issues": list(getattr(result, "issues", []) or []),
            "recommended_layout": list(getattr(result, "recommended_layout", []) or []),
            "naming_conventions": list(getattr(result, "naming_conventions", []) or []),
            "retention_rules": list(getattr(result, "retention_rules", []) or []),
            "priority_fixes": list(getattr(result, "priority_fixes", []) or []),
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
        rlm = agent.get_runtime_module("memory_structure_migration_plan")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(
                audit_findings=findings,
                approved_constraints=constraints,
            )

        raw_ops = list(getattr(result, "operations", []) or [])
        operations: list[dict[str, str]] = []
        for raw in raw_ops:
            row = raw if isinstance(raw, dict) else {"op": str(raw)}
            operations.append(
                {
                    "op": str(row.get("op", "")),
                    "src": str(row.get("src", "")),
                    "dst": str(row.get("dst", "")),
                    "reason": str(row.get("reason", "")),
                }
            )

        return {
            "status": "ok",
            "operations": operations,
            "rollback_steps": list(getattr(result, "rollback_steps", []) or []),
            "verification_checks": list(
                getattr(result, "verification_checks", []) or []
            ),
            "estimated_risk": str(getattr(result, "estimated_risk", "medium")),
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

        rlm = agent.get_runtime_module("clarification_questions")
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(
                ambiguous_request=request,
                available_context=available_context,
                operation_risk=risk_norm,
            )

        proceed_without_answer = _coerce_bool(
            getattr(result, "proceed_without_answer", False),
            default=False,
        )
        if risk_norm == "high":
            proceed_without_answer = False

        return {
            "status": "ok",
            "questions": list(getattr(result, "questions", []) or []),
            "blocking_unknowns": list(getattr(result, "blocking_unknowns", []) or []),
            "safe_default": str(getattr(result, "safe_default", "")),
            "proceed_without_answer": proceed_without_answer,
        }

    def rlm_query(query: str, context: str = "") -> dict[str, Any]:
        """Delegate a complex sub-task to a recursive sub-agent.

        Spawns a new independent RLM agent to solve the query.
        """
        if agent._current_depth >= agent._max_depth:
            return {
                "status": "error",
                "error": f"Max recursion depth ({agent._max_depth}) reached. Cannot spawn sub-agent.",
            }

        SubAgentClass = agent.__class__

        sub_agent = SubAgentClass(
            interpreter=agent.interpreter,
            max_depth=agent._max_depth,
            current_depth=agent._current_depth + 1,
        )

        prompt = query
        if context:
            prompt = f"Context:\n{context}\n\nTask: {query}"

        result = sub_agent.chat_turn(prompt)

        return {
            "status": "ok",
            "answer": result.get("assistant_response", ""),
            "sub_agent_history": sub_agent.history_turns(),
            "depth": agent._current_depth + 1,
        }

    # -- Sandbox editing -----------------------------------------------------

    def edit_file(path: str, old_snippet: str, new_snippet: str) -> dict[str, Any]:
        """Robustly edit a file by finding and replacing a unique text snippet.

        Fails if the old_snippet is not found or is not unique in the file.
        Use this over fragile `sed` commands for precise code editing.
        """
        code = """
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {path}")
    exit(0)

count = content.count(old_snippet)
if count == 0:
    SUBMIT(status="error", error="old_snippet not found in file")
elif count > 1:
    SUBMIT(status="error", error=f"old_snippet is ambiguous (found {count} times)")
else:
    new_content = content.replace(old_snippet, new_snippet)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    SUBMIT(status="ok", path=path, message="File updated successfully")
"""
        return execute_submit(
            agent,
            code,
            variables={
                "path": path,
                "old_snippet": old_snippet,
                "new_snippet": new_snippet,
            },
        )

    # -- Buffer & volume management ------------------------------------------

    def _resolve_volume_path(
        path: str,
        *,
        default_root: str = "/data/memory",
        allowed_root: str = "/data",
    ) -> str:
        """Resolve *path* to a normalized path inside the mounted Modal volume.

        Rules:
        - Absolute paths must already be within ``allowed_root``.
        - Relative paths are rooted under ``default_root``.
        - Parent traversal escaping ``allowed_root`` is rejected.
        """
        import posixpath

        raw = str(path or "").strip()
        if not raw:
            raise ValueError("Path cannot be empty.")

        allowed = str(PurePosixPath(allowed_root))
        default = str(PurePosixPath(default_root))
        candidate = PurePosixPath(raw)

        normalized = str(
            candidate if candidate.is_absolute() else PurePosixPath(default) / candidate
        )
        normalized = posixpath.normpath(normalized)
        normalized = str(PurePosixPath(normalized))

        if normalized != allowed and not normalized.startswith(allowed + "/"):
            raise ValueError(
                f"Path must stay within mounted volume root '{allowed}'. Got: {path}"
            )
        return normalized

    def read_buffer(name: str) -> dict[str, Any]:
        """Read the full contents of a sandbox buffer."""
        result = execute_submit(
            agent, "SUBMIT(items=get_buffer(name))", variables={"name": name}
        )
        items = result.get("items", [])
        return {"status": "ok", "name": name, "items": items, "count": len(items)}

    def clear_buffer(name: str = "") -> dict[str, Any]:
        """Clear one sandbox buffer (or all buffers when name is empty)."""
        if name:
            code = 'clear_buffer(name)\nSUBMIT(status="ok", scope="single", name=name)'
            variables: dict[str, Any] = {"name": name}
        else:
            code = 'clear_buffer()\nSUBMIT(status="ok", scope="all")'
            variables = {}
        return execute_submit(agent, code, variables=variables)

    def save_buffer_to_volume(name: str, path: str) -> dict[str, Any]:
        """Persist a sandbox buffer to Modal Volume storage as JSON."""
        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/workspace/buffers",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        result = execute_submit(
            agent,
            code,
            variables={"name": name, "path": resolved_path},
        )
        if result.get("status") == "ok" and agent.interpreter._volume:
            try:
                agent.interpreter.commit()
            except Exception:
                pass
        return result

    def load_text_from_volume(path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from Modal Volume into host-side document memory."""
        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/workspace",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        if agent.interpreter._volume:
            try:
                # Pull latest writes from other containers before reading.
                agent.interpreter.reload()
            except Exception:
                pass

        result = execute_submit(
            agent,
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": resolved_path},
        )
        text = str(result.get("text", ""))
        if text.startswith("[error:"):
            return {"status": "error", "error": text, "path": resolved_path}
        agent._set_document(alias, text)
        agent.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "path": resolved_path,
            "chars": len(text),
            "lines": len(text.splitlines()),
        }

    def process_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a document from volume and register it for downstream analysis."""
        loaded = load_text_from_volume(path, alias=alias)
        if loaded.get("status") != "ok":
            return loaded
        text = resolve_document(agent, alias)
        return {
            "status": "ok",
            "alias": alias,
            "path": loaded.get("path", path),
            "chars": len(text),
            "lines": len(text.splitlines()),
            "hint": "Use analyze_long_document or summarize_long_document for semantic processing.",
        }

    # -- Persistent memory management ----------------------------------------
    # These tools allow the agent to use the mounted volume as a persistent
    # "hard drive" for storing user profiles, archival documents, etc.

    def memory_read(path: str) -> dict[str, Any]:
        """Read a file from persistent memory (Modal Volume)."""
        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/memory",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        if agent.interpreter._volume:
            try:
                agent.interpreter.reload()
            except Exception:
                pass

        code = """
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    SUBMIT(status="ok", path=path, content=content, chars=len(content))
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {path}")
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        return execute_submit(agent, code, variables={"path": resolved_path})

    def memory_write(path: str, content: str) -> dict[str, Any]:
        """Write content to a file in persistent memory (Modal Volume)."""
        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/memory",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        code = """
import os
import subprocess
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # Sync filesystem to volume if supported (optional but good for safety)
    try:
        os.sync()
    except AttributeError:
        pass
    sync_rc = 0
    try:
        proc = subprocess.run(["sync", "/data"], check=False, capture_output=True)
        sync_rc = int(proc.returncode)
    except Exception:
        sync_rc = -1
    SUBMIT(status="ok", path=path, chars=len(content), sync_rc=sync_rc)
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        # Note: Modal volumes are eventually consistent, but os.sync() helps.
        # The Interpreter also exposes a .commit() method if needed on the host side.
        result = execute_submit(
            agent,
            code,
            variables={"path": resolved_path, "content": content},
        )

        # Trigger explicit commit on the host side for immediate persistence
        if result.get("status") == "ok":
            if agent.interpreter._volume:
                try:
                    agent.interpreter.commit()
                except Exception:
                    pass  # Ignore commit errors, best effort
        return result

    def write_to_file(path: str, content: str, append: bool = False) -> dict[str, Any]:
        """Write/append text to a file in Modal Volume with safe path handling."""
        if not append:
            return memory_write(path=path, content=content)

        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/memory",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        code = """
import os
import subprocess
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
    try:
        os.sync()
    except AttributeError:
        pass
    sync_rc = 0
    try:
        proc = subprocess.run(["sync", "/data"], check=False, capture_output=True)
        sync_rc = int(proc.returncode)
    except Exception:
        sync_rc = -1
    SUBMIT(status="ok", path=path, chars=len(content), mode="append", sync_rc=sync_rc)
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        result = execute_submit(
            agent,
            code,
            variables={"path": resolved_path, "content": content},
        )
        if result.get("status") == "ok" and agent.interpreter._volume:
            try:
                agent.interpreter.commit()
            except Exception:
                pass
        return result

    def edit_core_memory(
        section: str,
        content: str,
        mode: str = "append",
    ) -> dict[str, Any]:
        """Edit core memory via append/replace operations with validation."""
        mode_norm = mode.strip().lower()
        if mode_norm not in {"append", "replace"}:
            return {
                "status": "error",
                "error": "mode must be one of: append, replace",
            }

        message = (
            agent.core_memory_append(section, content)
            if mode_norm == "append"
            else agent.core_memory_replace(section, content)
        )
        if message.startswith("Error:"):
            return {"status": "error", "error": message}

        return {
            "status": "ok",
            "section": section,
            "mode": mode_norm,
            "message": message,
            "chars": len(agent._core_memory.get(section, "")),
        }

    def memory_list(path: str = ".") -> dict[str, Any]:
        """List files and directories in persistent memory."""
        try:
            resolved_path = (
                _resolve_volume_path(
                    path,
                    default_root="/data/memory",
                    allowed_root="/data",
                )
                if path.strip() not in {"", ".", "./"}
                else "/data/memory"
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        if agent.interpreter._volume:
            try:
                agent.interpreter.reload()
            except Exception:
                pass

        code = """
import os
try:
    items = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        kind = "dir" if os.path.isdir(full) else "file"
        items.append({"name": name, "type": kind})
    SUBMIT(status="ok", path=path, items=items, count=len(items))
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        return execute_submit(agent, code, variables={"path": resolved_path})

    # -- Assemble tool list --------------------------------------------------

    from dspy import Tool

    return [
        Tool(
            parallel_semantic_map,
            name="parallel_semantic_map",
            desc="Run parallel semantic analysis over chunks via llm_query_batched",
        ),
        Tool(
            analyze_long_document,
            name="analyze_long_document",
            desc="Analyze a long document with the AnalyzeLongDocument RLM signature",
        ),
        Tool(
            summarize_long_document,
            name="summarize_long_document",
            desc="Summarize a long document with the SummarizeLongDocument RLM signature",
        ),
        Tool(
            extract_from_logs,
            name="extract_from_logs",
            desc="Extract structured patterns from log text via ExtractFromLogs RLM signature",
        ),
        Tool(
            grounded_answer,
            name="grounded_answer",
            desc="Answer a query from chunked evidence with structured citations",
        ),
        Tool(
            triage_incident_logs,
            name="triage_incident_logs",
            desc="Triage logs into severity, causes, impacted components, and actions",
        ),
        Tool(
            plan_code_change,
            name="plan_code_change",
            desc="Generate a structured implementation plan for a code-change task",
        ),
        Tool(
            propose_core_memory_update,
            name="propose_core_memory_update",
            desc="Propose keep/update/remove changes for core memory blocks",
        ),
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
        Tool(
            rlm_query,
            name="rlm_query",
            desc="Delegate a complex sub-task to a recursive sub-agent",
        ),
        Tool(
            edit_file,
            name="edit_file",
            desc="Robustly edit a file by finding and replacing a unique text snippet",
        ),
        Tool(
            read_buffer,
            name="read_buffer",
            desc="Read the full contents of a sandbox buffer",
        ),
        Tool(
            clear_buffer,
            name="clear_buffer",
            desc="Clear one sandbox buffer (or all buffers when name is empty)",
        ),
        Tool(
            save_buffer_to_volume,
            name="save_buffer_to_volume",
            desc="Persist a sandbox buffer to Modal Volume storage as JSON",
        ),
        Tool(
            load_text_from_volume,
            name="load_text_from_volume",
            desc="Load text from Modal Volume into host-side document memory",
        ),
        Tool(
            process_document,
            name="process_document",
            desc="Load a document from Modal Volume and register it for analysis",
        ),
        Tool(
            memory_read,
            name="memory_read",
            desc="Read a file from persistent memory (Modal Volume)",
        ),
        Tool(
            memory_write,
            name="memory_write",
            desc="Write content to a file in persistent memory (Modal Volume)",
        ),
        Tool(
            write_to_file,
            name="write_to_file",
            desc="Write or append text to a file in persistent memory (Modal Volume)",
        ),
        Tool(
            edit_core_memory,
            name="edit_core_memory",
            desc="Edit core memory blocks using append or replace mode",
        ),
        Tool(
            memory_list,
            name="memory_list",
            desc="List files and directories in persistent memory",
        ),
    ]
