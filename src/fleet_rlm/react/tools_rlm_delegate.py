"""RLM delegation tools for sandbox-based agent operations.

These tools delegate work to recursive sub-agents \u2014 each tool spawns a new
``RLMReActChatAgent`` at ``current_depth + 1`` (true recursion), mirroring
the ``rlm_query`` pattern.  The sub-agent receives a structured prompt and
has access to the full ReAct tool set.

Extracted from tools_sandbox.py as part of the modularization effort.
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


def _handle_sub_agent_response(
    agent: "RLMReActChatAgent",
    result: dict[str, Any],
    default_response: dict[str, Any],
    fallback_fields: list[str],
    include_trajectory: bool,
    doc_len: int | None = None,
) -> dict[str, Any]:
    """Generic handler for mapping sub-agent responses to expected tool dicts."""
    if result.get("status") == "error":
        return result

    response_text = result.get("assistant_response", "")
    trajectory = result.get("trajectory", {})

    response = default_response.copy()
    response["status"] = "ok"

    if response_text:
        for field in fallback_fields:
            if field in response and isinstance(response[field], list):
                response[field] = [response_text]
            else:
                response[field] = response_text

    if doc_len is not None:
        response["doc_chars"] = doc_len

    response["depth"] = result.get("depth", getattr(agent, "_current_depth", 0) + 1)
    response["sub_agent_history"] = result.get("sub_agent_history", 0)

    if include_trajectory:
        response.update(_rlm_trajectory_payload_from_dict(trajectory, include=True))

    return response


def _rlm_trajectory_payload_from_dict(
    trajectory: Any, *, include: bool
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

    return {
        "trajectory_steps": len(steps),
        "trajectory": steps,
    }


def build_rlm_delegate_tools(agent: "RLMReActChatAgent") -> list[Any]:
    """Build RLM delegation tools bound to *agent*."""

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
                f"Query: {query}\nChunk index: {idx}\nReturn concise findings as plain text.\n\n{chunk_item[:6000]}"
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
        query: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        """Analyze a long document via a recursive sub-agent."""
        agent.start()
        document = resolve_document(agent, alias)
        prompt = (
            f"You have a document loaded as 'active'. Analyze it thoroughly.\n\n"
            f"Query: {query}\n\n"
            "Provide your response with:\n"
            "- findings: a list of key findings\n"
            "- answer: a comprehensive answer to the query\n"
            "- sections_examined: number of sections you examined"
        )
        result = spawn_delegate_sub_agent(
            agent, prompt=prompt, document=document, document_alias=alias
        )
        default_resp = {"findings": [], "answer": "", "sections_examined": 0}
        return _handle_sub_agent_response(
            agent,
            result,
            default_resp,
            ["findings", "answer"],
            include_trajectory,
            len(document),
        )

    def summarize_long_document(
        focus: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        """Summarize a long document via a recursive sub-agent."""
        agent.start()
        document = resolve_document(agent, alias)
        prompt = (
            f"You have a document loaded as 'active'. Summarize it.\n\n"
            f"Focus: {focus}\n\n"
            "Provide your response with:\n"
            "- summary: a concise summary\n"
            "- key_points: a list of key points\n"
            "- coverage_pct: estimated percentage of document covered (integer)"
        )
        result = spawn_delegate_sub_agent(
            agent, prompt=prompt, document=document, document_alias=alias
        )
        default_resp = {"summary": "", "key_points": [], "coverage_pct": 0}
        return _handle_sub_agent_response(
            agent,
            result,
            default_resp,
            ["summary", "key_points"],
            include_trajectory,
            len(document),
        )

    def extract_from_logs(
        query: str, alias: str = "active", include_trajectory: bool = True
    ) -> dict[str, Any]:
        """Extract structured patterns from log text via a recursive sub-agent."""
        agent.start()
        document = resolve_document(agent, alias)
        prompt = (
            f"You have log text loaded as 'active'. Extract structured patterns.\n\n"
            f"Query: {query}\n\n"
            "Provide your response with:\n"
            "- matches: a list of matching entries\n"
            "- patterns: a list of identified patterns\n"
            "- time_range: the time range covered"
        )
        result = spawn_delegate_sub_agent(
            agent, prompt=prompt, document=document, document_alias=alias
        )
        default_resp = {"matches": [], "patterns": [], "time_range": "unknown"}
        return _handle_sub_agent_response(
            agent,
            result,
            default_resp,
            ["matches", "patterns"],
            include_trajectory,
            len(document),
        )

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
            return {"status": "error", "error": "Invalid max_chunks value."}

        agent.start()
        document = resolve_document(agent, alias)
        chunks = chunk_text(
            document, chunk_strategy, size=80_000, overlap=1_000, pattern=""
        )
        evidence_chunks = [chunk_to_text(chunk) for chunk in chunks][:max_chunks_int]
        if not evidence_chunks:
            return {"status": "error", "error": "No evidence chunks available."}

        chunks_text = "\n\n---CHUNK BOUNDARY---\n\n".join(
            f"[Chunk {i}]\n{c}" for i, c in enumerate(evidence_chunks)
        )

        prompt = (
            "You have evidence chunks from a document. Answer the query with explicit citations.\n\n"
            f"Query: {query}\n\nEvidence chunks:\n{chunks_text}\n\n"
            "Provide your response with:\n- answer: your grounded answer\n"
            "- citations: list of dicts with keys source, chunk_id, evidence, reason\n"
            "- confidence: integer 0-100\n- coverage_notes: notes on evidence coverage"
        )

        result = spawn_delegate_sub_agent(agent, prompt=prompt)
        if result.get("status") == "error":
            return result

        response_text = result.get("assistant_response", "")
        structured = parse_json_from_response(response_text) or {}

        return {
            "status": "ok",
            "answer": str(structured.get("answer", response_text)),
            "citations": structured.get("citations", [])
            if isinstance(structured.get("citations"), list)
            else [],
            "confidence": max(
                0,
                min(
                    100,
                    int(structured.get("confidence", 0))
                    if isinstance(structured.get("confidence"), (int, str))
                    and str(structured.get("confidence")).isdigit()
                    else 0,
                ),
            ),
            "coverage_notes": str(structured.get("coverage_notes", "")),
            "doc_chars": len(document),
            "depth": result.get("depth", getattr(agent, "_current_depth", 0) + 1),
            "sub_agent_history": result.get("sub_agent_history", 0),
            **(
                _rlm_trajectory_payload_from_dict(
                    result.get("trajectory", {}), include=include_trajectory
                )
                if include_trajectory
                else {}
            ),
        }

    def triage_incident_logs(
        query: str,
        alias: str = "active",
        service_context: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Triage logs into severity, causes, impact, and actions via a recursive sub-agent."""
        agent.start()
        document = resolve_document(agent, alias)
        prompt = (
            f"You have incident logs loaded as 'active'. Triage them.\n\n"
            f"Query: {query}\nService context: {service_context or 'not provided'}\n\n"
            "Provide your response with:\n- severity: one of low, medium, high, critical\n"
            "- probable_root_causes: list of causes\n- impacted_components: list of affected components\n"
            "- recommended_actions: list of actions\n- time_range: the time range covered"
        )
        result = spawn_delegate_sub_agent(
            agent, prompt=prompt, document=document, document_alias=alias
        )
        default_resp = {
            "severity": "low",
            "probable_root_causes": [],
            "impacted_components": [],
            "recommended_actions": [],
            "time_range": "unknown",
        }
        return _handle_sub_agent_response(
            agent,
            result,
            default_resp,
            ["probable_root_causes"],
            include_trajectory,
            len(document),
        )

    def plan_code_change(
        task: str,
        repo_context: str = "",
        constraints: str = "",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Build a structured code-change plan via a recursive sub-agent."""
        agent.start()
        prompt = (
            f"Plan a code change.\n\nTask: {task}\nRepo context: {repo_context or 'None'}\n"
            f"Constraints: {constraints or 'Keep changes minimal.'}\n\n"
            "Provide your response with:\n- plan_steps: ordered list of implementation steps\n"
            "- files_to_touch: list of files to modify\n- validation_commands: list of commands\n- risks: list of risks"
        )
        result = spawn_delegate_sub_agent(agent, prompt=prompt)
        default_resp = {
            "plan_steps": [],
            "files_to_touch": [],
            "validation_commands": [],
            "risks": [],
        }
        return _handle_sub_agent_response(
            agent, result, default_resp, ["plan_steps"], include_trajectory
        )

    def propose_core_memory_update(include_trajectory: bool = True) -> dict[str, Any]:
        """Propose safe updates to core memory via a recursive sub-agent."""
        agent.start()
        turn_lines = [
            f"Turn {i}\n{turn}"
            for i, turn in enumerate(agent.history_messages()[-20:], 1)
        ]
        turn_history = "\n\n".join(turn_lines) or "No recent turns."

        prompt = (
            "Review the conversation history and current core memory, then propose updates.\n\n"
            f"Turn history:\n{turn_history}\n\nCurrent core memory:\n{agent.fmt_core_memory()}\n\n"
            "Provide your response with:\n- keep: list of memory blocks to keep unchanged\n"
            "- update: list of memory blocks to update\n- remove: list of memory blocks to remove\n"
            "- rationale: explanation for the proposed changes"
        )
        result = spawn_delegate_sub_agent(agent, prompt=prompt)
        default_resp = {"keep": [], "update": [], "remove": [], "rationale": ""}
        return _handle_sub_agent_response(
            agent, result, default_resp, ["rationale", "update"], include_trajectory
        )

    def rlm_query(query: str, context: str = "") -> dict[str, Any]:
        """Delegate a complex sub-task to a recursive sub-agent."""
        prompt = f"Context:\n{context}\n\nTask: {query}" if context else query
        result = spawn_delegate_sub_agent(agent, prompt=prompt)
        if result.get("status") == "error":
            return result
        return {
            "status": "ok",
            "answer": result.get("assistant_response", ""),
            "sub_agent_history": result.get("sub_agent_history", 0),
            "depth": result.get("depth", getattr(agent, "_current_depth", 0) + 1),
        }

    from dspy import Tool

    return [
        Tool(parallel_semantic_map, name="parallel_semantic_map"),
        Tool(analyze_long_document, name="analyze_long_document"),
        Tool(summarize_long_document, name="summarize_long_document"),
        Tool(extract_from_logs, name="extract_from_logs"),
        Tool(grounded_answer, name="grounded_answer"),
        Tool(triage_incident_logs, name="triage_incident_logs"),
        Tool(plan_code_change, name="plan_code_change"),
        Tool(propose_core_memory_update, name="propose_core_memory_update"),
        Tool(rlm_query, name="rlm_query"),
    ]
