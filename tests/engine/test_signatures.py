"""Signatures live in one module with stable field names; the stopping rules exist once."""

from fleet_rlm.engine import signatures
from fleet_rlm.engine.signatures import STOPPING_RULES, TRIAGE_ROUTE, FleetTask, TurnTriage


def test_triage_signature_keeps_the_typed_choice_and_criteria():
    route_field = TurnTriage.model_fields["route"]
    decision = TRIAGE_ROUTE(value="rlm", confidence=0.91)
    assert route_field.annotation is TRIAGE_ROUTE
    assert "direct" in route_field.json_schema_extra["desc"]
    assert decision.value == "rlm"
    assert TRIAGE_ROUTE.criteria() == {
        "direct": "Simple response requiring no code, files, or Skills",
        "flex": "A task needing one bounded Python or Skill workflow",
        "rlm": "A multi-part task requiring an independent child investigation",
    }


def test_fleet_task_fields_are_stable_for_compiled_programs():
    assert list(FleetTask.input_fields) == [
        "workspace_manifest",
        "skills_catalog",
        "memory_summary",
        "session_history",
        "execution_route",
        "task",
        "context",
    ]
    assert list(FleetTask.output_fields) == ["answer", "learned_skill_note"]


def test_stopping_rules_have_one_source():
    from fleet_rlm.interpreter import DaytonaSandboxManager

    factory = DaytonaSandboxManager("key", "org", "volume", "sess_sig").interpreter_factory()
    assert STOPPING_RULES in FleetTask.instructions
    assert factory.execution_instructions.endswith(STOPPING_RULES)


def test_child_and_judge_signatures_are_named():
    assert list(signatures.ChildTask.output_fields) == ["findings"]
    assert list(signatures.ContextualChildTask.input_fields) == ["context_hint", "subtask"]
    assert list(signatures.AnswerJudge.output_fields) == ["is_correct", "is_safe", "critique"]
    assert list(signatures.item_decision(["a", "b"]).output_fields) == ["decision"]
