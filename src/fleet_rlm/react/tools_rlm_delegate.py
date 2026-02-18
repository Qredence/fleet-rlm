"""RLM delegation tools for sandbox-based agent operations.

These tools delegate work to recursive sub-agents — each tool spawns a new
``RLMReActChatAgent`` at ``current_depth + 1`` (true recursion), mirroring
the ``rlm_query`` pattern.  The sub-agent receives a structured prompt and
has access to the full ReAct tool set.

Extracted from tools_sandbox.py as part of the modularization effort
(Linear: QRE-273).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .delegate_sub_agent import parse_json_from_response, spawn_delegate_sub_agent
from .tools import (
    chunk_text,
    chunk_to_text,
    execute_submit,
    resolve_document,
)

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


def _resolve_depth_hint(result: dict[str, Any], *, fallback_depth: int) -> int:
    raw_depth = result.get("depth", fallback_depth)
    if isinstance(raw_depth, bool):
        return fallback_depth
    if isinstance(raw_depth, (int, float)):
        return int(raw_depth)
    if isinstance(raw_depth, str):
        stripped = raw_depth.strip()
        if stripped.isdigit():
            return int(stripped)
    return fallback_depth


def _parent_step_hint(result: dict[str, Any]) -> str | None:
    raw_parent = result.get("parent_step_id")
    if isinstance(raw_parent, str) and raw_parent:
        return raw_parent
    return None


def build_rlm_delegate_tools(agent: "RLMReActChatAgent") -> list[Any]:
    """Build RLM delegation tools bound to *agent*.

    These tools delegate to recursive sub-agents.  Each tool:
    1. Starts the parent agent if needed
    2. Resolves documents or prepares inputs
    3. Spawns a sub-agent with a structured prompt
    4. Normalizes the sub-agent response into the expected shape

    Returns a list of ``dspy.Tool`` wrappers ready to be appended to the
    main tool list built by ``build_tool_list``.
    """

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
        """Analyze a long document via a recursive sub-agent."""
        agent.start()
        document = resolve_document(agent, alias)

        prompt = (
            "You have a document loaded as 'active'. Analyze it thoroughly.\n\n"
            f"Query: {query}\n\n"
            "Provide your response with:\n"
            "- findings: a list of key findings\n"
            "- answer: a comprehensive answer to the query\n"
            "- sections_examined: number of sections you examined"
        )

        result = spawn_delegate_sub_agent(
            agent,
            prompt=prompt,
            document=document,
            document_alias=alias,
        )

        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        trajectory = result.get("trajectory", {})
        depth_hint = _resolve_depth_hint(
            result, fallback_depth=agent._current_depth + 1
        )
        parent_step_id = _parent_step_hint(result)

        response: dict[str, Any] = {
            "status": "ok",
            "findings": [response_text] if response_text else [],
            "answer": response_text,
            "sections_examined": 0,
            "doc_chars": len(document),
            "depth": depth_hint,
            "sub_agent_history": result.get("sub_agent_history", 0),
        }
        if parent_step_id:
            response["parent_step_id"] = parent_step_id
        if include_trajectory:
            response.update(
                _rlm_trajectory_payload_from_dict(
                    trajectory,
                    include=True,
                    depth=depth_hint,
                    parent_step_id=parent_step_id,
                )
            )
        return response

    def summarize_long_document(
        focus: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Summarize a long document via a recursive sub-agent."""
        agent.start()
        document = resolve_document(agent, alias)

        prompt = (
            "You have a document loaded as 'active'. Summarize it.\n\n"
            f"Focus: {focus}\n\n"
            "Provide your response with:\n"
            "- summary: a concise summary\n"
            "- key_points: a list of key points\n"
            "- coverage_pct: estimated percentage of document covered (integer)"
        )

        result = spawn_delegate_sub_agent(
            agent,
            prompt=prompt,
            document=document,
            document_alias=alias,
        )

        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        trajectory = result.get("trajectory", {})
        depth_hint = _resolve_depth_hint(
            result, fallback_depth=agent._current_depth + 1
        )
        parent_step_id = _parent_step_hint(result)

        response: dict[str, Any] = {
            "status": "ok",
            "summary": response_text,
            "key_points": [],
            "coverage_pct": 0,
            "doc_chars": len(document),
            "depth": depth_hint,
            "sub_agent_history": result.get("sub_agent_history", 0),
        }
        if parent_step_id:
            response["parent_step_id"] = parent_step_id
        if include_trajectory:
            response.update(
                _rlm_trajectory_payload_from_dict(
                    trajectory,
                    include=True,
                    depth=depth_hint,
                    parent_step_id=parent_step_id,
                )
            )
        return response

    def extract_from_logs(
        query: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Extract structured patterns from log text via a recursive sub-agent."""
        agent.start()
        logs = resolve_document(agent, alias)

        prompt = (
            "You have log text loaded as 'active'. Extract structured patterns.\n\n"
            f"Query: {query}\n\n"
            "Provide your response with:\n"
            "- matches: a list of matching entries\n"
            "- patterns: a list of identified patterns\n"
            "- time_range: the time range covered"
        )

        result = spawn_delegate_sub_agent(
            agent,
            prompt=prompt,
            document=logs,
            document_alias=alias,
        )

        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        trajectory = result.get("trajectory", {})
        depth_hint = _resolve_depth_hint(
            result, fallback_depth=agent._current_depth + 1
        )
        parent_step_id = _parent_step_hint(result)

        response: dict[str, Any] = {
            "status": "ok",
            "matches": [response_text] if response_text else [],
            "patterns": [],
            "time_range": "unknown",
            "depth": depth_hint,
            "sub_agent_history": result.get("sub_agent_history", 0),
        }
        if parent_step_id:
            response["parent_step_id"] = parent_step_id
        if include_trajectory:
            response.update(
                _rlm_trajectory_payload_from_dict(
                    trajectory,
                    include=True,
                    depth=depth_hint,
                    parent_step_id=parent_step_id,
                )
            )
        return response

    def grounded_answer(
        query: str,
        alias: str = "active",
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Answer a query with explicit machine-readable citations via a recursive sub-agent."""
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

        chunks_text = "\n\n---CHUNK BOUNDARY---\n\n".join(
            f"[Chunk {i}]\n{c}" for i, c in enumerate(evidence_chunks)
        )

        prompt = (
            "You have evidence chunks from a document. Answer the query with "
            "explicit citations.\n\n"
            f"Query: {query}\n\n"
            f"Evidence chunks:\n{chunks_text}\n\n"
            "Provide your response with:\n"
            "- answer: your grounded answer\n"
            "- citations: list of dicts with keys source, chunk_id, evidence, reason\n"
            "- confidence: integer 0-100\n"
            "- coverage_notes: notes on evidence coverage"
        )

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        structured = parse_json_from_response(response_text) or {}
        citations = structured.get("citations", [])
        if not isinstance(citations, list):
            citations = []

        confidence_raw = structured.get("confidence", 0)
        try:
            confidence = int(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0
        confidence = max(0, min(100, confidence))

        coverage_notes = structured.get("coverage_notes", "")
        if not isinstance(coverage_notes, str):
            coverage_notes = str(coverage_notes)

        answer_value = structured.get("answer", response_text)
        answer_text = str(answer_value) if answer_value is not None else ""
        trajectory = result.get("trajectory", {})
        depth_hint = _resolve_depth_hint(
            result, fallback_depth=agent._current_depth + 1
        )
        parent_step_id = _parent_step_hint(result)

        response: dict[str, Any] = {
            "status": "ok",
            "answer": answer_text,
            "citations": citations,
            "confidence": confidence,
            "coverage_notes": coverage_notes,
            "doc_chars": len(document),
            "depth": depth_hint,
            "sub_agent_history": result.get("sub_agent_history", 0),
        }
        if parent_step_id:
            response["parent_step_id"] = parent_step_id
        if include_trajectory:
            response.update(
                _rlm_trajectory_payload_from_dict(
                    trajectory,
                    include=True,
                    depth=depth_hint,
                    parent_step_id=parent_step_id,
                )
            )
        return response

    def triage_incident_logs(
        query: str,
        alias: str = "active",
        service_context: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Triage logs into severity, causes, impact, and actions via a recursive sub-agent."""
        agent.start()
        logs = resolve_document(agent, alias)

        prompt = (
            "You have incident logs loaded as 'active'. Triage them.\n\n"
            f"Query: {query}\n"
            f"Service context: {service_context or 'not provided'}\n\n"
            "Provide your response with:\n"
            "- severity: one of low, medium, high, critical\n"
            "- probable_root_causes: list of causes\n"
            "- impacted_components: list of affected components\n"
            "- recommended_actions: list of actions\n"
            "- time_range: the time range covered"
        )

        result = spawn_delegate_sub_agent(
            agent,
            prompt=prompt,
            document=logs,
            document_alias=alias,
        )

        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        trajectory = result.get("trajectory", {})
        depth_hint = _resolve_depth_hint(
            result, fallback_depth=agent._current_depth + 1
        )
        parent_step_id = _parent_step_hint(result)

        response: dict[str, Any] = {
            "status": "ok",
            "severity": "low",
            "probable_root_causes": [response_text] if response_text else [],
            "impacted_components": [],
            "recommended_actions": [],
            "time_range": "unknown",
            "depth": depth_hint,
            "sub_agent_history": result.get("sub_agent_history", 0),
        }
        if parent_step_id:
            response["parent_step_id"] = parent_step_id
        if include_trajectory:
            response.update(
                _rlm_trajectory_payload_from_dict(
                    trajectory,
                    include=True,
                    depth=depth_hint,
                    parent_step_id=parent_step_id,
                )
            )
        return response

    def plan_code_change(
        task: str,
        repo_context: str = "",
        constraints: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Build a structured code-change plan via a recursive sub-agent."""
        agent.start()
        context_text = repo_context or "No additional repo context provided."
        constraints_text = (
            constraints or "Keep changes minimal and backward compatible."
        )

        prompt = (
            "Plan a code change.\n\n"
            f"Task: {task}\n"
            f"Repo context: {context_text}\n"
            f"Constraints: {constraints_text}\n\n"
            "Provide your response with:\n"
            "- plan_steps: ordered list of implementation steps\n"
            "- files_to_touch: list of files to modify\n"
            "- validation_commands: list of commands to validate the change\n"
            "- risks: list of potential risks"
        )

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        trajectory = result.get("trajectory", {})
        depth_hint = _resolve_depth_hint(
            result, fallback_depth=agent._current_depth + 1
        )
        parent_step_id = _parent_step_hint(result)

        response: dict[str, Any] = {
            "status": "ok",
            "plan_steps": [response_text] if response_text else [],
            "files_to_touch": [],
            "validation_commands": [],
            "risks": [],
            "depth": depth_hint,
            "sub_agent_history": result.get("sub_agent_history", 0),
        }
        if parent_step_id:
            response["parent_step_id"] = parent_step_id
        if include_trajectory:
            response.update(
                _rlm_trajectory_payload_from_dict(
                    trajectory,
                    include=True,
                    depth=depth_hint,
                    parent_step_id=parent_step_id,
                )
            )
        return response

    def propose_core_memory_update(
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Propose safe updates to core memory via a recursive sub-agent."""
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

        prompt = (
            "Review the conversation history and current core memory, then "
            "propose updates.\n\n"
            f"Turn history:\n{turn_history}\n\n"
            f"Current core memory:\n{current_memory}\n\n"
            "Provide your response with:\n"
            "- keep: list of memory blocks to keep unchanged\n"
            "- update: list of memory blocks to update\n"
            "- remove: list of memory blocks to remove\n"
            "- rationale: explanation for the proposed changes"
        )

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        trajectory = result.get("trajectory", {})
        depth_hint = _resolve_depth_hint(
            result, fallback_depth=agent._current_depth + 1
        )
        parent_step_id = _parent_step_hint(result)

        response: dict[str, Any] = {
            "status": "ok",
            "keep": [],
            "update": [response_text] if response_text else [],
            "remove": [],
            "rationale": response_text,
            "depth": depth_hint,
            "sub_agent_history": result.get("sub_agent_history", 0),
        }
        if parent_step_id:
            response["parent_step_id"] = parent_step_id
        if include_trajectory:
            response.update(
                _rlm_trajectory_payload_from_dict(
                    trajectory,
                    include=True,
                    depth=depth_hint,
                    parent_step_id=parent_step_id,
                )
            )
        return response

    def rlm_query(query: str, context: str = "") -> dict[str, Any]:
        """Delegate a complex sub-task to a recursive sub-agent.

        Spawns a new independent RLM agent to solve the query.
        """
        prompt = query
        if context:
            prompt = f"Context:\n{context}\n\nTask: {query}"

        result = spawn_delegate_sub_agent(agent, prompt=prompt)

        if result.get("status") == "error":
            return result

        depth_hint = _resolve_depth_hint(
            result, fallback_depth=agent._current_depth + 1
        )
        response = {
            "status": "ok",
            "answer": result.get("assistant_response", ""),
            "sub_agent_history": result.get("sub_agent_history", 0),
            "depth": depth_hint,
        }
        parent_step_id = _parent_step_hint(result)
        if parent_step_id:
            response["parent_step_id"] = parent_step_id
        return response

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
            desc="Analyze a long document via a recursive sub-agent",
        ),
        Tool(
            summarize_long_document,
            name="summarize_long_document",
            desc="Summarize a long document via a recursive sub-agent",
        ),
        Tool(
            extract_from_logs,
            name="extract_from_logs",
            desc="Extract structured patterns from log text via a recursive sub-agent",
        ),
        Tool(
            grounded_answer,
            name="grounded_answer",
            desc="Answer a query from chunked evidence with structured citations via a recursive sub-agent",
        ),
        Tool(
            triage_incident_logs,
            name="triage_incident_logs",
            desc="Triage logs into severity, causes, impacted components, and actions via a recursive sub-agent",
        ),
        Tool(
            plan_code_change,
            name="plan_code_change",
            desc="Generate a structured implementation plan for a code-change task via a recursive sub-agent",
        ),
        Tool(
            propose_core_memory_update,
            name="propose_core_memory_update",
            desc="Propose keep/update/remove changes for core memory blocks via a recursive sub-agent",
        ),
        Tool(
            rlm_query,
            name="rlm_query",
            desc="Delegate a complex sub-task to a recursive sub-agent",
        ),
    ]


def _rlm_trajectory_payload_from_dict(
    trajectory: Any,
    *,
    include: bool,
    depth: int | None = None,
    parent_step_id: str | None = None,
) -> dict[str, Any]:
    """Build trajectory payload from a sub-agent trajectory dict."""
    if not include:
        return {}

    if isinstance(trajectory, dict):
        steps = list(trajectory.values()) if trajectory else []
    elif isinstance(trajectory, list):
        steps = trajectory
    else:
        steps = []

    payload: dict[str, Any] = {
        "trajectory_steps": len(steps),
        "trajectory": steps,
    }
    if depth is not None:
        payload["depth"] = depth
    if parent_step_id:
        payload["parent_step_id"] = parent_step_id
    return payload
