"""DSPy signatures for production/runtime RLM workflows.

This module contains canonical signatures used by the interactive ReAct chat
surface, runtime modules, long-context analysis flows, grounding/citation
workflows, incident triage, and memory intelligence helpers.
"""

from __future__ import annotations

from typing import Literal, TypedDict

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
    """Interactive ReAct chat signature with explicit conversation history."""

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
    severity: Literal["low", "medium", "high", "critical"] = dspy.OutputField(
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
    """Generate a structured implementation plan for a code change."""

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
    action_type: Literal[
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
    ] = dspy.OutputField(
        desc="Action type: read, write, append, move, delete, mkdir, tree, audit, migrate, noop"
    )
    target_paths: list[str] = dspy.OutputField(desc="Paths involved in the action")
    content_plan: list[str] = dspy.OutputField(
        desc="Planned content operations if applicable"
    )
    risk_level: Literal["low", "medium", "high"] = dspy.OutputField(
        desc="Risk level: low, medium, high"
    )
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


class ReflectAndReviseWorkspaceStep(dspy.Signature):
    """Choose whether one recursive workspace step should recurse, repair, or finalize."""

    user_request: str = dspy.InputField(desc="Current delegated user or parent request")
    working_memory_summary: str = dspy.InputField(
        desc="Summary or handles for Daytona-backed long memory and workspace state"
    )
    current_plan: str = dspy.InputField(
        desc="Current execution plan or delegate context summary"
    )
    latest_sandbox_evidence: str = dspy.InputField(
        desc="Selected sandbox evidence snippets or runtime metadata summary"
    )
    latest_tool_or_code_result: str = dspy.InputField(
        desc="Latest tool, code, or child-result summary"
    )
    loop_state: str = dspy.InputField(
        desc="Current recursion depth, retry budget, and loop state summary"
    )
    next_action: Literal[
        "recurse",
        "finalize",
        "request_human_review",
        "repair_and_retry",
    ] = dspy.OutputField(
        desc="Next step: recurse, finalize, request_human_review, or repair_and_retry"
    )
    revised_plan: str = dspy.OutputField(
        desc="Updated plan for the next recursive step or repair attempt"
    )
    rationale: str = dspy.OutputField(desc="Why this action should happen next")
    confidence: float = dspy.OutputField(desc="Decision confidence from 0.0 to 1.0")


class PlanRecursiveSubqueries(dspy.Signature):
    """Plan bounded semantic decomposition for the next recursive workspace pass."""

    user_request: str = dspy.InputField(desc="Current delegated user or parent request")
    assembled_recursive_context: str = dspy.InputField(
        desc="Bounded recursive context assembled from Daytona-backed handles and summaries"
    )
    current_plan: str = dspy.InputField(
        desc="Current execution plan or delegate context summary"
    )
    loop_state: str = dspy.InputField(
        desc="Current recursion depth, retry budget, and loop state summary"
    )
    latest_sandbox_evidence: str = dspy.InputField(
        desc="Latest bounded sandbox, tool, or code evidence summary available before the next pass"
    )
    subquery_budget: int = dspy.InputField(
        desc="Maximum number of bounded semantic subqueries allowed for the next pass"
    )
    decomposition_mode: Literal["single_pass", "fan_out"] = dspy.OutputField(
        desc="Whether the next recursive pass should stay single_pass or fan_out into bounded subqueries"
    )
    subqueries: list[str] = dspy.OutputField(
        desc="Bounded semantic subqueries or subproblems for the next recursive pass"
    )
    batching_strategy: str = dspy.OutputField(
        desc="How Python/runtime should batch the proposed subqueries, for example serial or batched"
    )
    aggregation_plan: str = dspy.OutputField(
        desc="How Python/runtime should aggregate the subquery results for the parent recursive state"
    )
    decomposition_rationale: str = dspy.OutputField(
        desc="Why this decomposition shape fits the current recursive state"
    )


class AssembleRecursiveWorkspaceContext(dspy.Signature):
    """Select bounded Daytona-backed memory/evidence for the next recursive pass."""

    user_request: str = dspy.InputField(desc="Current delegated user or parent request")
    current_plan: str = dspy.InputField(
        desc="Current execution plan or delegate context summary"
    )
    loop_state: str = dspy.InputField(
        desc="Current recursion depth, retry budget, and loop state summary"
    )
    working_memory_catalog: list[str] = dspy.InputField(
        desc="Compact catalog of Daytona-backed memory handles or bounded memory summaries"
    )
    recent_sandbox_evidence_catalog: list[str] = dspy.InputField(
        desc="Compact catalog of recent sandbox, tool, or code evidence summaries"
    )
    latest_tool_or_code_result: str = dspy.InputField(
        desc="Latest tool, code, or child-result summary"
    )
    context_budget: int = dspy.InputField(
        desc="Maximum target character budget for the next recursive context"
    )
    selected_memory_handles: list[str] = dspy.OutputField(
        desc="Most relevant Daytona-backed memory handles or memory section handles to surface next"
    )
    selected_evidence_ids: list[str] = dspy.OutputField(
        desc="Most relevant recent evidence ids to surface next"
    )
    assembled_context_summary: str = dspy.OutputField(
        desc="Bounded summary describing what the next recursive pass should see"
    )
    omission_rationale: str = dspy.OutputField(
        desc="Why other memory/evidence was omitted to control context size"
    )


class VerifyRecursiveAggregation(dspy.Signature):
    """Semantically verify bounded decomposition-driven recursive results."""

    user_request: str = dspy.InputField(desc="Current delegated user or parent request")
    assembled_recursive_context: str = dspy.InputField(
        desc="Bounded recursive context assembled from Daytona-backed handles and summaries"
    )
    decomposition_plan_summary: str = dspy.InputField(
        desc="Bounded summary of the recursive decomposition and Python/runtime aggregation plan"
    )
    collected_subquery_outputs: list[str] = dspy.InputField(
        desc="Bounded summaries of collected subquery outputs prepared by Python/runtime"
    )
    latest_sandbox_evidence: str = dspy.InputField(
        desc="Latest bounded sandbox, tool, or code evidence summary relevant to the aggregate"
    )
    verification_status: Literal[
        "sufficient",
        "needs_repair",
        "needs_more_recursion",
        "needs_human_review",
    ] = dspy.OutputField(
        desc="Whether the aggregated result is sufficient, needs repair, needs more recursion, or needs human review"
    )
    missing_evidence: list[str] = dspy.OutputField(
        desc="Key missing evidence or checks that weaken the current aggregate"
    )
    contradictions: list[str] = dspy.OutputField(
        desc="Conflicts or inconsistencies detected across the bounded subquery outputs"
    )
    verified_summary: str = dspy.OutputField(
        desc="Concise verified synthesis that is safe to pass into recursive reflection"
    )
    verification_rationale: str = dspy.OutputField(
        desc="Why the aggregate is or is not strong enough for the next recursive step"
    )


class PlanRecursiveRepair(dspy.Signature):
    """Plan bounded semantic repair when a recursive result is still insufficient."""

    user_request: str = dspy.InputField(desc="Current delegated user or parent request")
    assembled_recursive_context: str = dspy.InputField(
        desc="Bounded recursive context assembled from Daytona-backed handles and summaries"
    )
    verification_summary: str = dspy.InputField(
        desc="Bounded summary of verification, reflection, and insufficiency signals"
    )
    latest_sandbox_evidence: str = dspy.InputField(
        desc="Latest bounded sandbox, tool, or code evidence summary relevant to repair"
    )
    latest_failure_signals: str = dspy.InputField(
        desc="Compact recent failure signals, contradictions, or missing evidence summaries"
    )
    repair_budget: int = dspy.InputField(
        desc="Maximum number of bounded repair steps or repair subqueries Python/runtime may execute"
    )
    repair_mode: Literal[
        "targeted_repair",
        "bounded_repair_loop",
        "needs_more_recursion",
        "needs_human_review",
        "no_repair",
    ] = dspy.OutputField(
        desc="Whether to do a targeted repair, bounded repair loop, escalate, or skip repair"
    )
    repair_target: str = dspy.OutputField(
        desc="The narrow workspace/code/tool target that should be repaired first"
    )
    repair_steps: list[str] = dspy.OutputField(
        desc="Bounded repair steps for Python/runtime to execute inside the existing Daytona-backed loop"
    )
    repair_subqueries: list[str] = dspy.OutputField(
        desc="Optional bounded repair subqueries for Python/runtime child delegation"
    )
    repair_rationale: str = dspy.OutputField(
        desc="Why this repair plan is appropriate and why it should stay narrow or escalate"
    )


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


class RLMLargeDocSignature(dspy.Signature):
    """Fetch and process an oversized URL document using the REPL.

    All input fields are stored as REPL variables — the LLM sees only
    metadata and writes Python to stream-fetch the URL, chunk it, and call
    ``sub_rlm()`` per chunk.  The ``history`` variable provides session
    context so the LLM can target extraction at what the user actually needs.
    """

    task: str = dspy.InputField(
        desc="Instruction for how to process the document"
    )
    prompt: str = dspy.InputField(
        desc="The URL to fetch (stored as REPL variable)"
    )
    history: dspy.History = dspy.InputField(
        desc="Prior chat turns for user intent context (keys: user_request, assistant_response)"
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
    "AssembleRecursiveWorkspaceContext",
    "PlanRecursiveRepair",
    "PlanRecursiveSubqueries",
    "MemoryActionIntentSignature",
    "MemoryMigrationOperation",
    "MemoryStructureAuditSignature",
    "MemoryStructureMigrationPlanSignature",
    "ReflectAndReviseWorkspaceStep",
    "RLMLargeDocSignature",
    "RLMReActChatSignature",
    "RLMVariableSignature",
    "RecursiveSubQuerySignature",
    "SummarizeLongDocument",
    "VerifyRecursiveAggregation",
    "VolumeFileTreeSignature",
    "VolumeTreeNode",
]
