from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.react.signatures import (
    AnalyzeLongDocument,
    CodeChangePlan,
    ClarificationQuestionSignature,
    CoreMemoryUpdateProposal,
    ExtractFromLogs,
    GroundedAnswerWithCitations,
    IncidentTriageFromLogs,
    MemoryActionIntentSignature,
    MemoryStructureAuditSignature,
    MemoryStructureMigrationPlanSignature,
    SummarizeLongDocument,
    VolumeFileTreeSignature,
)


@pytest.mark.parametrize(
    ("module_name", "signature_cls", "call_kwargs", "expected_field"),
    [
        (
            "AnalyzeLongDocumentModule",
            AnalyzeLongDocument,
            {"document": "doc", "query": "q"},
            "findings",
        ),
        (
            "SummarizeLongDocumentModule",
            SummarizeLongDocument,
            {"document": "doc", "focus": "f"},
            "summary",
        ),
        (
            "ExtractFromLogsModule",
            ExtractFromLogs,
            {"logs": "l1\nl2", "query": "err"},
            "matches",
        ),
        (
            "GroundedAnswerWithCitationsModule",
            GroundedAnswerWithCitations,
            {
                "query": "q",
                "evidence_chunks": ["c1"],
                "response_style": "concise",
            },
            "citations",
        ),
        (
            "IncidentTriageFromLogsModule",
            IncidentTriageFromLogs,
            {"logs": "l1", "service_context": "svc", "query": "err"},
            "severity",
        ),
        (
            "CodeChangePlanModule",
            CodeChangePlan,
            {"task": "add x", "repo_context": "ctx", "constraints": "none"},
            "plan_steps",
        ),
        (
            "CoreMemoryUpdateProposalModule",
            CoreMemoryUpdateProposal,
            {"turn_history": "h", "current_memory": "m"},
            "keep",
        ),
        (
            "VolumeFileTreeModule",
            VolumeFileTreeSignature,
            {"root_path": "/data/memory", "max_depth": 2, "include_hidden": False},
            "nodes",
        ),
        (
            "MemoryActionIntentModule",
            MemoryActionIntentSignature,
            {
                "user_request": "archive old files",
                "current_tree": [{"path": "/data/memory/a.txt", "type": "file"}],
                "policy_constraints": "confirm before delete",
            },
            "action_type",
        ),
        (
            "MemoryStructureAuditModule",
            MemoryStructureAuditSignature,
            {
                "tree_snapshot": [{"path": "/data/memory", "type": "dir"}],
                "usage_goals": "keep organized",
            },
            "issues",
        ),
        (
            "MemoryStructureMigrationPlanModule",
            MemoryStructureMigrationPlanSignature,
            {"audit_findings": ["issue"], "approved_constraints": "safe only"},
            "operations",
        ),
        (
            "ClarificationQuestionModule",
            ClarificationQuestionSignature,
            {
                "ambiguous_request": "clean memory",
                "available_context": "few files",
                "operation_risk": "high",
            },
            "questions",
        ),
    ],
)
def test_runtime_module_wraps_rlm(
    monkeypatch, module_name, signature_cls, call_kwargs, expected_field
):
    from fleet_rlm.react import rlm_runtime_modules as runtime_mod

    created = {}

    class _FakeRLM:
        def __init__(
            self, *, signature, interpreter, max_iterations, max_llm_calls, verbose
        ):
            created["signature"] = signature
            created["interpreter"] = interpreter
            created["max_iterations"] = max_iterations
            created["max_llm_calls"] = max_llm_calls
            created["verbose"] = verbose

        def __call__(self, **kwargs):
            created["call_kwargs"] = kwargs
            if expected_field == "findings":
                return SimpleNamespace(findings=["x"], answer="a", sections_examined=1)
            if expected_field == "summary":
                return SimpleNamespace(summary="s", key_points=["k"], coverage_pct=42)
            if expected_field == "matches":
                return SimpleNamespace(
                    matches=["m"], patterns={"error": "m"}, time_range="t"
                )
            if expected_field == "citations":
                return SimpleNamespace(
                    answer="a",
                    citations=[
                        {"source": "s", "chunk_id": "0", "evidence": "e", "reason": "r"}
                    ],
                    confidence=88,
                    coverage_notes="ok",
                )
            if expected_field == "severity":
                return SimpleNamespace(
                    severity="high",
                    probable_root_causes=["r"],
                    impacted_components=["c"],
                    recommended_actions=["a"],
                    time_range="t",
                )
            if expected_field == "plan_steps":
                return SimpleNamespace(
                    plan_steps=["p"],
                    files_to_touch=["f.py"],
                    validation_commands=["pytest -q"],
                    risks=["r"],
                )
            if expected_field == "nodes":
                return SimpleNamespace(
                    nodes=[
                        {
                            "path": "/data/memory/a.txt",
                            "type": "file",
                            "size_bytes": "12",
                            "depth": "1",
                        }
                    ],
                    total_files=1,
                    total_dirs=1,
                    truncated=False,
                )
            if expected_field == "action_type":
                return SimpleNamespace(
                    action_type="delete",
                    target_paths=["/data/memory/a.txt"],
                    content_plan=[],
                    risk_level="high",
                    requires_confirmation=True,
                    rationale="destructive action",
                )
            if expected_field == "issues":
                return SimpleNamespace(
                    issues=["flat hierarchy"],
                    recommended_layout=["/data/memory/projects"],
                    naming_conventions=["snake_case"],
                    retention_rules=["archive >30d"],
                    priority_fixes=["group files"],
                )
            if expected_field == "operations":
                return SimpleNamespace(
                    operations=[
                        {
                            "op": "move",
                            "src": "/data/memory/a.txt",
                            "dst": "/data/memory/archive/a.txt",
                            "reason": "organize",
                        }
                    ],
                    rollback_steps=["move back"],
                    verification_checks=["path exists"],
                    estimated_risk="medium",
                )
            if expected_field == "questions":
                return SimpleNamespace(
                    questions=["Which files exactly?"],
                    blocking_unknowns=["target set"],
                    safe_default="no-op",
                    proceed_without_answer=False,
                )
            return SimpleNamespace(
                keep=["k"],
                update=["u"],
                remove=["r"],
                rationale="why",
            )

    monkeypatch.setattr(runtime_mod.dspy, "RLM", _FakeRLM)

    cls = getattr(runtime_mod, module_name)
    module = cls(
        interpreter=object(),
        max_iterations=17,
        max_llm_calls=19,
        verbose=True,
    )
    result = module(**call_kwargs)

    assert created["signature"] is signature_cls
    assert created["max_iterations"] == 17
    assert created["max_llm_calls"] == 19
    assert created["verbose"] is True
    assert created["call_kwargs"] == call_kwargs
    assert hasattr(result, expected_field)
