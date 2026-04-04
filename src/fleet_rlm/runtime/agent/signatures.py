"""DSPy signatures for production/runtime RLM workflows.

This module contains canonical signatures used by the interactive ReAct chat
surface, runtime modules, long-context analysis flows, grounding/citation
workflows, incident triage, and memory intelligence helpers.
"""

from __future__ import annotations

from typing import TypedDict

import dspy


class GroundedCitation(TypedDict):
    """Canonical machine-readable citation record for grounded answers."""

    source: str
    chunk_id: str
    evidence: str
    reason: str


class VolumeTreeNode(TypedDict):
    """Canonical bounded file-tree node used by memory volume signatures."""

    path: str
    type: str
    size_bytes: int
    depth: int


class MemoryMigrationOperation(TypedDict):
    """Canonical reversible memory-layout migration operation."""

    op: str
    src: str
    dst: str
    reason: str


class RLMReActChatSignature(dspy.Signature):
    """Interactive ReAct chat signature with explicit conversation history.
    You have the ability to spin up long-running daemon servers (like 'npm run dev'). Use `start_background_process` and iteratively check `read_process_logs` to ensure a server boots successfully.

    For large multi-part deliverables on Daytona (books, reports, multi-file analyses),
    prefer a single interpreter-driven workflow that loops in Python and uses
    `llm_query_batched()` for structurally parallel semantic subtasks instead of
    repeating many top-level ReAct iterations for each section. Use the persistent
    interpreter state to accumulate and merge results before responding.
    """

    user_request: str = dspy.InputField(desc="Current user request in the chat session")
    core_memory: str = dspy.InputField(
        desc="Persistent memory blocks (Persona, Human, Scratchpad) that define your identity and context"
    )
    history: dspy.History = dspy.InputField(
        desc="Prior chat turns using keys user_request and assistant_response"
    )
    assistant_response: str = dspy.OutputField(desc="Final assistant response to user")


class SummarizeLongDocument(dspy.Signature):
    """Summarize a long document with controllable focus.

    The LLM should chunk the document, query each chunk with the given
    focus topic, and merge the per-chunk summaries into a coherent
    whole.

    Input Fields:
        document: Full text of the document
        focus: Topic or aspect to focus the summary on

    Output Fields:
        summary: Coherent summary text
        key_points: Bullet-point list of key takeaways
        coverage_pct: Estimated percentage of the document covered
    """

    document: str = dspy.InputField(desc="Full document text")
    focus: str = dspy.InputField(desc="Topic or aspect to focus on")
    summary: str = dspy.OutputField(desc="Coherent summary text")
    key_points: list[str] = dspy.OutputField(desc="Bullet-point list of key takeaways")
    coverage_pct: int = dspy.OutputField(
        desc="Estimated percentage of document covered (0-100)"
    )


class ExtractFromLogs(dspy.Signature):
    """Extract patterns from log-style text.

    The LLM should use ``grep`` and ``chunk_by_headers`` helpers to
    search for the query pattern, categorise matches, and identify any
    time-range information.

    Input Fields:
        logs: Full log text (loaded in sandbox)
        query: What to search for

    Output Fields:
        matches: List of matching log entries
        patterns: Dict mapping pattern category to example entries
        time_range: Observed time range of matching entries
    """

    logs: str = dspy.InputField(desc="Full log text")
    query: str = dspy.InputField(desc="Pattern or topic to search for")
    matches: list[str] = dspy.OutputField(desc="List of matching log entries")
    patterns: dict[str, str] = dspy.OutputField(
        desc="Dict mapping category to example entries"
    )
    time_range: str = dspy.OutputField(desc="Observed time range of matching entries")


class GroundedAnswerWithCitations(dspy.Signature):
    """Answer questions using chunked evidence and explicit citations.

    The model should ground each key claim in evidence from input chunks
    and produce machine-readable citations for downstream display.
    """

    query: str = dspy.InputField(desc="Question to answer from evidence")
    evidence_chunks: list[str] = dspy.InputField(
        desc="Relevant evidence chunks to ground the answer"
    )
    response_style: str = dspy.InputField(
        desc="Response style preference such as concise or verbose"
    )
    answer: str = dspy.OutputField(desc="Grounded answer synthesized from evidence")
    citations: list[GroundedCitation] = dspy.OutputField(
        desc="Citation dicts with keys: source, chunk_id, evidence, reason"
    )
    confidence: int = dspy.OutputField(desc="Estimated confidence score from 0 to 100")
    coverage_notes: str = dspy.OutputField(
        desc="Notes on coverage gaps or uncertainty in available evidence"
    )


class IncidentTriageFromLogs(dspy.Signature):
    """Triage incident logs into operator-ready diagnostics."""

    logs: str = dspy.InputField(desc="Raw log text to analyze")
    service_context: str = dspy.InputField(
        desc="Service/environment context to guide triage"
    )
    query: str = dspy.InputField(desc="Primary investigation question")
    severity: str = dspy.OutputField(
        desc="Incident severity: low, medium, high, or critical"
    )
    probable_root_causes: list[str] = dspy.OutputField(
        desc="Likely root causes inferred from log evidence"
    )
    impacted_components: list[str] = dspy.OutputField(
        desc="Components likely affected by the incident"
    )
    recommended_actions: list[str] = dspy.OutputField(
        desc="Concrete next actions for mitigation and follow-up"
    )
    time_range: str = dspy.OutputField(desc="Observed incident time range")


class CodeChangePlan(dspy.Signature):
    """Generate a structured implementation plan for a code change.
    When analyzing large Python codebases, prefer using the `extract_python_ast` tool to quickly map out structural architectures before dropping down into raw regex grepping.
    You have the ability to spin up long-running development servers or background processes. Use `start_background_process` and iteratively check `read_process_logs` to ensure a server compiles successfully before declaring a coding task finished.
    """

    task: str = dspy.InputField(desc="Requested coding task")
    repo_context: str = dspy.InputField(
        desc="Repository/domain context relevant to the task"
    )
    constraints: str = dspy.InputField(desc="Constraints or non-goals to respect")
    plan_steps: list[str] = dspy.OutputField(desc="Ordered implementation steps")
    files_to_touch: list[str] = dspy.OutputField(
        desc="Likely files/modules that should be modified"
    )
    validation_commands: list[str] = dspy.OutputField(
        desc="Commands to verify correctness before completion"
    )
    risks: list[str] = dspy.OutputField(desc="Key risks and failure modes to monitor")


class CoreMemoryUpdateProposal(dspy.Signature):
    """Propose safe updates to core memory state from conversation history."""

    turn_history: str = dspy.InputField(desc="Recent interaction history summary")
    current_memory: str = dspy.InputField(desc="Current core memory snapshot")
    keep: list[str] = dspy.OutputField(desc="Memory items to keep unchanged")
    update: list[str] = dspy.OutputField(desc="Memory items to update")
    remove: list[str] = dspy.OutputField(desc="Memory items to remove")
    rationale: str = dspy.OutputField(desc="Reasoning behind the proposal")


class VolumeFileTreeSignature(dspy.Signature):
    """Build a bounded, structured file-tree view for a volume path."""

    root_path: str = dspy.InputField(desc="Root path to traverse")
    max_depth: int = dspy.InputField(desc="Maximum directory depth to include")
    include_hidden: bool = dspy.InputField(desc="Whether to include hidden files")
    nodes: list[VolumeTreeNode] = dspy.OutputField(
        desc="Tree nodes with keys path, type, size_bytes, depth"
    )
    total_files: int = dspy.OutputField(desc="Total file count discovered")
    total_dirs: int = dspy.OutputField(desc="Total directory count discovered")
    truncated: bool = dspy.OutputField(
        desc="True when traversal stops early due to node limits"
    )


class MemoryActionIntentSignature(dspy.Signature):
    """Classify memory action intent and risk from user request + tree context."""

    user_request: str = dspy.InputField(desc="Original user request")
    current_tree: list[VolumeTreeNode] = dspy.InputField(
        desc="Current memory tree snapshot"
    )
    policy_constraints: str = dspy.InputField(desc="Policy and safety constraints")
    action_type: str = dspy.OutputField(
        desc="Action type: read, write, append, move, delete, mkdir, tree, audit, migrate, noop"
    )
    target_paths: list[str] = dspy.OutputField(desc="Paths involved in the action")
    content_plan: list[str] = dspy.OutputField(
        desc="Planned content operations if applicable"
    )
    risk_level: str = dspy.OutputField(desc="Risk level: low, medium, high")
    requires_confirmation: bool = dspy.OutputField(
        desc="Whether explicit confirmation should be required"
    )
    rationale: str = dspy.OutputField(desc="Why this action and risk were selected")


class MemoryStructureAuditSignature(dspy.Signature):
    """Audit memory structure and propose conventions/layout improvements."""

    tree_snapshot: list[VolumeTreeNode] = dspy.InputField(
        desc="Current tree snapshot of the memory directory"
    )
    usage_goals: str = dspy.InputField(desc="Goals for memory organization")
    issues: list[str] = dspy.OutputField(desc="Detected structure issues")
    recommended_layout: list[str] = dspy.OutputField(
        desc="Recommended target layout paths and groupings"
    )
    naming_conventions: list[str] = dspy.OutputField(
        desc="Recommended naming conventions"
    )
    retention_rules: list[str] = dspy.OutputField(desc="Retention/archive policies")
    priority_fixes: list[str] = dspy.OutputField(
        desc="Highest-priority fixes to apply first"
    )


class MemoryStructureMigrationPlanSignature(dspy.Signature):
    """Generate a reversible migration plan from memory structure findings."""

    audit_findings: list[str] = dspy.InputField(desc="Audit issues/findings")
    approved_constraints: str = dspy.InputField(
        desc="Approved constraints for migration operations"
    )
    operations: list[MemoryMigrationOperation] = dspy.OutputField(
        desc="Operations with keys op, src, dst, reason"
    )
    rollback_steps: list[str] = dspy.OutputField(
        desc="Rollback steps in reverse operation order"
    )
    verification_checks: list[str] = dspy.OutputField(
        desc="Post-migration checks to verify integrity"
    )
    estimated_risk: str = dspy.OutputField(desc="Estimated migration risk level")


class ClarificationQuestionSignature(dspy.Signature):
    """Generate clarification questions for ambiguous or risky operations."""

    ambiguous_request: str = dspy.InputField(desc="Potentially ambiguous request")
    available_context: str = dspy.InputField(desc="Known contextual constraints/state")
    operation_risk: str = dspy.InputField(desc="Estimated operation risk level")
    questions: list[str] = dspy.OutputField(desc="Clarification questions to ask user")
    blocking_unknowns: list[str] = dspy.OutputField(
        desc="Unknowns that block safe execution"
    )
    safe_default: str = dspy.OutputField(desc="Safe default behavior if unanswered")
    proceed_without_answer: bool = dspy.OutputField(
        desc="Whether execution can proceed safely without user response"
    )


class RecursiveSubQuerySignature(dspy.Signature):
    """Answer a bounded recursive sub-problem by exploring context in Python."""

    prompt: str = dspy.InputField(desc="The delegated sub-task to solve")
    context: str = dspy.InputField(desc="Optional context for the sub-task")
    answer: str = dspy.OutputField(desc="Answer for the parent caller")


class RLMVariableSignature(dspy.Signature):
    """Explore and answer questions about an arbitrarily long prompt.

    The ``prompt`` field is stored as a REPL variable — use code to slice,
    search, and aggregate it.  Call ``sub_rlm(text)`` for recursive semantic
    processing of chunks, ``llm_query(text)`` for single LLM calls, and
    ``SUBMIT(answer=...)`` to return the result.

    Per Algorithm 1 (arXiv 2512.24601v2): dspy.RLM stores input fields in
    the REPL automatically — the LLM sees only metadata (type, length,
    preview) and explores data through code execution.
    """

    task: str = dspy.InputField(desc="The question or instruction to accomplish")
    prompt: str = dspy.InputField(
        desc="The full text to process (stored as REPL variable, not in LLM context)"
    )
    answer: str = dspy.OutputField(
        desc="Final answer (call SUBMIT(answer=...) in REPL)"
    )


__all__ = [
    "ClarificationQuestionSignature",
    "CodeChangePlan",
    "CoreMemoryUpdateProposal",
    "ExtractFromLogs",
    "GroundedAnswerWithCitations",
    "GroundedCitation",
    "IncidentTriageFromLogs",
    "MemoryActionIntentSignature",
    "MemoryMigrationOperation",
    "MemoryStructureAuditSignature",
    "MemoryStructureMigrationPlanSignature",
    "RLMReActChatSignature",
    "RLMVariableSignature",
    "RecursiveSubQuerySignature",
    "SummarizeLongDocument",
    "VolumeFileTreeSignature",
    "VolumeTreeNode",
]
