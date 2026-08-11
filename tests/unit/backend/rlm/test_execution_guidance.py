"""Default RLM execution guidance stays relevance-driven and capability-complete."""

from __future__ import annotations

from fleet_rlm.files.workspace_models import DAYTONA_WORKSPACE_CAPABILITY, UNAVAILABLE_WORKSPACE_CAPABILITY
from fleet_rlm.rlm.signature import FleetRLMSignature


def test_default_signature_orders_capabilities_before_semantic_calls() -> None:
    instructions = FleetRLMSignature.instructions
    normalized_instructions = " ".join(instructions.split())

    ordered_markers = (
        "Python standard library",
        "Load Session History, Skills, Attachments, URL content, or Session Workspace content only",
        "llm_query(prompt)",
        "llm_query_batched(prompts)",
        "exactly one typed ``SUBMIT``",
    )
    positions = tuple(instructions.index(marker) for marker in ordered_markers)

    assert positions == tuple(sorted(positions))
    assert "load Skill ``dspy-rlm``" not in instructions
    assert "Root LM plans and verifies" in instructions
    assert "Sub LM performs bounded semantic analysis" in instructions
    assert "untrusted context" in instructions
    assert "do not spend an iteration probing optional packages" in instructions
    assert "do not submit in the initial" in instructions
    assert "independent invariant" in instructions
    assert "known reference prefix" in instructions
    assert "Never pass positional arguments" in instructions
    assert "SUBMIT(answer=answer)" in instructions
    assert "Once sufficient verification exists, the next action must contain ``SUBMIT``" in normalized_instructions
    assert "Never spend an iteration only restating a verified result or emitting empty code" in normalized_instructions
    assert "Never repeat an identical interpreter action" in normalized_instructions


def test_default_signature_marks_discovery_inputs_as_conditional_metadata() -> None:
    context_desc = str(FleetRLMSignature.input_fields["session_context"].json_schema_extra["desc"])
    skills_desc = str(FleetRLMSignature.input_fields["skill_cards"].json_schema_extra["desc"])
    attachments_desc = str(FleetRLMSignature.input_fields["attachments"].json_schema_extra["desc"])

    assert "untrusted" in context_desc
    assert "only when" in context_desc
    assert "only when" in skills_desc
    assert "only when" in attachments_desc


def test_workspace_capability_declares_temporary_durable_and_commit_gated_state() -> None:
    daytona = DAYTONA_WORKSPACE_CAPABILITY.instructions
    unavailable = UNAVAILABLE_WORKSPACE_CAPABILITY.instructions

    for marker in ("REPL variables", "sandbox-local files", "immediately durable", "Turn Commit"):
        assert marker in daytona
    assert "unavailable" in unavailable
    assert "REPL variables" in unavailable
