"""DSPy signatures for information extraction tasks.

This module defines DSPy Signature classes that specify the input/output
structure for various information extraction tasks using the Recursive
Language Model (RLM).

Each signature defines:
    - Input fields: What data the model receives
    - Output fields: What structured data the model should extract

Available signatures:
    - ExtractArchitecture: Extract DSPy framework architecture details
    - ExtractAPIEndpoints: Extract API endpoint definitions
    - FindErrorPatterns: Identify and categorize error patterns
    - ExtractWithCustomTool: Extract patterns using regex tool assistance
"""

from __future__ import annotations

import dspy


class ExtractArchitecture(dspy.Signature):
    """Extract architectural information from DSPy documentation.

    Analyzes documentation to identify the modular components of the DSPy
    framework, available optimizers, and key design principles.

    Input Fields:
        docs: Full DSPy documentation text to analyze
        query: Specific extraction query or focus area

    Output Fields:
        modules: List of DSPy modules identified in documentation
        optimizers: List of available optimizers and their purposes
        design_principles: Key design principles and architectural decisions
    """

    docs: str = dspy.InputField(desc="Full DSPy documentation text")
    query: str = dspy.InputField(desc="What to extract")
    modules: list[str] = dspy.OutputField(desc="List of DSPy modules")
    optimizers: list[str] = dspy.OutputField(desc="List of optimizers")
    design_principles: str = dspy.OutputField(desc="Key design principles")


class ExtractAPIEndpoints(dspy.Signature):
    """Extract API endpoints using batched analysis.

    Scans documentation to identify and catalog API endpoints,
    their parameters, and usage patterns.

    Input Fields:
        docs: API documentation text to analyze

    Output Fields:
        api_endpoints: List of API endpoints with details
    """

    docs: str = dspy.InputField(desc="API documentation")
    api_endpoints: list[str] = dspy.OutputField(desc="List of API endpoints")


class FindErrorPatterns(dspy.Signature):
    """Find and categorize error patterns in documentation.

    Identifies common errors, their causes, and solutions from
    troubleshooting documentation or error guides.

    Input Fields:
        docs: Documentation text containing error information

    Output Fields:
        error_categories: Dictionary mapping error types to solutions
        total_errors_found: Total number of errors identified
    """

    docs: str = dspy.InputField(desc="Documentation text")
    error_categories: dict[str, str] = dspy.OutputField(
        desc="Error types mapped to solutions"
    )
    total_errors_found: int = dspy.OutputField(desc="Total errors identified")


class ExtractWithCustomTool(dspy.Signature):
    """Extract specific patterns using custom regex tool.

    Uses the regex_extract tool to parse documentation structure,
    finding markdown headers and code blocks for structural analysis.

    Strategy:
        1. Use regex_extract() to find all markdown headers
        2. Use regex_extract() to find all code blocks
        3. Summarize document structure

    Input Fields:
        docs: Documentation to analyze

    Output Fields:
        headers: All markdown headers found in the document
        code_blocks: All code block languages/content found
        structure_summary: Summary of document organization
    """

    docs: str = dspy.InputField(desc="Documentation to analyze")
    headers: list[str] = dspy.OutputField(desc="All markdown headers found")
    code_blocks: list[str] = dspy.OutputField(desc="All code block languages found")
    structure_summary: str = dspy.OutputField(desc="Summary of document structure")


class AnalyzeLongDocument(dspy.Signature):
    """Analyze a long document by navigating, querying, and synthesizing.

    The LLM should use sandbox helpers (``peek``, ``grep``,
    ``chunk_by_size``, ``chunk_by_headers``) to explore the document
    programmatically, call ``llm_query`` on relevant sections, and
    aggregate findings via ``SUBMIT``.

    Input Fields:
        document: Full text of the document loaded into the sandbox
        query: What to find or analyse

    Output Fields:
        findings: List of extracted facts / answers
        answer: Synthesised prose answer
        sections_examined: How many sections were inspected
    """

    document: str = dspy.InputField(desc="Full document text (loaded in sandbox)")
    query: str = dspy.InputField(desc="Analysis query or question")
    findings: list[str] = dspy.OutputField(desc="List of extracted facts / answers")
    answer: str = dspy.OutputField(desc="Synthesised prose answer")
    sections_examined: int = dspy.OutputField(desc="Number of sections inspected")


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
    citations: list[dict[str, str]] = dspy.OutputField(
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
    nodes: list[dict[str, str]] = dspy.OutputField(
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
    current_tree: list[dict[str, str]] = dspy.InputField(
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

    tree_snapshot: list[dict[str, str]] = dspy.InputField(
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
    operations: list[dict[str, str]] = dspy.OutputField(
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
