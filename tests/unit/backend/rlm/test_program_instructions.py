"""Behavior contracts for program instructions."""

from __future__ import annotations

from fleet_rlm.rlm.program import (
    BASE_RLM_INSTRUCTIONS,
    DISCOVERY_RLM_INSTRUCTIONS,
    RECURSION_RLM_INSTRUCTIONS,
    REPL_RLM_INSTRUCTIONS,
    TOOL_RLM_INSTRUCTIONS,
    TOOL_RLM_INSTRUCTIONS_NO_DISPATCH,
    WORKSPACE_MUTATION_RLM_INSTRUCTIONS,
    FleetProgramSpec,
    FleetRLMSignature,
    RLMOptions,
    build_program,
    compose_rlm_instructions,
    fleet_rlm_instruction_fragments,
    root_signature_for_recursion,
)
from fleet_rlm.workspace.models import DAYTONA_WORKSPACE_CAPABILITY, UNAVAILABLE_WORKSPACE_CAPABILITY


def test_default_fleet_signature_omits_recursive_fragments() -> None:
    fragments = fleet_rlm_instruction_fragments(recursion_enabled=False)

    assert fragments.base == BASE_RLM_INSTRUCTIONS
    assert fragments.repl == REPL_RLM_INSTRUCTIONS
    assert fragments.tools == TOOL_RLM_INSTRUCTIONS
    assert fragments.recursion is None
    assert FleetRLMSignature.instructions == fragments.compose()
    assert "rlm_query(capsule=capsule)" not in FleetRLMSignature.instructions
    assert "5. Verify within the same action" in FleetRLMSignature.instructions


def test_nonrecursive_root_signature_omits_only_the_optional_recursion_fragment() -> None:
    recursive = compose_rlm_instructions(recursion_enabled=True)
    nonrecursive = root_signature_for_recursion(FleetRLMSignature, recursion_enabled=False).instructions

    assert "rlm_query(capsule=capsule)" not in nonrecursive
    assert "5. Verify within the same action" in nonrecursive
    assert RECURSION_RLM_INSTRUCTIONS in recursive
    assert RECURSION_RLM_INSTRUCTIONS not in nonrecursive
    assert nonrecursive.endswith(DISCOVERY_RLM_INSTRUCTIONS)


def test_custom_output_fields_stay_stable_while_fleet_policy_is_composed() -> None:
    import dspy

    class CustomResult(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    recursive = root_signature_for_recursion(CustomResult, recursion_enabled=True)
    nonrecursive = root_signature_for_recursion(CustomResult, recursion_enabled=False)

    assert recursive is not CustomResult
    assert recursive.input_fields.keys() == CustomResult.input_fields.keys()
    assert recursive.output_fields.keys() == CustomResult.output_fields.keys()
    assert "rlm_query(capsule=capsule)" in recursive.instructions
    assert "rlm_query(capsule=capsule)" not in nonrecursive.instructions


def test_fragment_composition_preserves_established_instruction_text() -> None:
    enabled = compose_rlm_instructions(recursion_enabled=True)
    disabled = compose_rlm_instructions(recursion_enabled=False)
    assert enabled.startswith(BASE_RLM_INSTRUCTIONS)
    assert REPL_RLM_INSTRUCTIONS in enabled
    assert TOOL_RLM_INSTRUCTIONS in enabled
    assert RECURSION_RLM_INSTRUCTIONS in enabled
    assert RECURSION_RLM_INSTRUCTIONS not in disabled
    assert DISCOVERY_RLM_INSTRUCTIONS in disabled


def test_batch_read_instruction_requires_the_registered_tool() -> None:
    absent = root_signature_for_recursion(FleetRLMSignature, recursion_enabled=False).instructions
    present = root_signature_for_recursion(
        FleetRLMSignature,
        recursion_enabled=False,
        tool_names=frozenset({"read_workspace_text_batch"}),
    ).instructions

    assert "read_workspace_text_batch" not in absent
    assert "read_workspace_text_batch" in present


def test_workspace_mutation_instruction_requires_a_registered_write_tool() -> None:
    absent = root_signature_for_recursion(FleetRLMSignature, recursion_enabled=False).instructions
    present = root_signature_for_recursion(
        FleetRLMSignature,
        recursion_enabled=False,
        tool_names=frozenset({"append_workspace_text"}),
    ).instructions
    publish_only = root_signature_for_recursion(
        FleetRLMSignature,
        recursion_enabled=False,
        tool_names=frozenset({"publish_workspace_artifact"}),
    ).instructions

    assert WORKSPACE_MUTATION_RLM_INSTRUCTIONS not in absent
    assert WORKSPACE_MUTATION_RLM_INSTRUCTIONS in present
    assert WORKSPACE_MUTATION_RLM_INSTRUCTIONS in publish_only
    assert "named write or publish remains" in present
    assert "Sandbox-local ``open()`` is not Session Workspace" in present


def test_tool_instructions_require_defensive_fetch_and_bounded_precision() -> None:
    assert "workspace_path" in TOOL_RLM_INSTRUCTIONS
    assert "workspace reference" in TOOL_RLM_INSTRUCTIONS
    assert "smallest" in TOOL_RLM_INSTRUCTIONS and "guard band" in TOOL_RLM_INSTRUCTIONS
    assert "never recompute a cached prefix" in TOOL_RLM_INSTRUCTIONS
    assert "pass that string unchanged" in TOOL_RLM_INSTRUCTIONS
    assert "pass them unchanged and in the given order" in TOOL_RLM_INSTRUCTIONS
    assert "do not omit listed accumulator updates" in TOOL_RLM_INSTRUCTIONS
    assert "request as unused text" in TOOL_RLM_INSTRUCTIONS


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
    assert "Verify within the same action when possible" in normalized_instructions
    assert "later iteration only when verification cannot be completed" in normalized_instructions
    assert "do not submit in the initial" not in instructions
    assert "independent invariant" in instructions
    assert "known reference prefix" in instructions
    assert "Never pass positional arguments" in instructions
    assert "SUBMIT(answer=answer)" in instructions
    assert "json.dumps(..., ensure_ascii=False)" in instructions
    assert "json.dumps(answer, ensure_ascii=False)" in instructions
    assert "Use ``indent=2`` only when" in normalized_instructions
    assert "Python ``repr`` text" in instructions
    assert (
        "Once the request is fully satisfied and sufficient verification exists, "
        "the next action must contain ``SUBMIT``"
    ) in normalized_instructions
    assert "after completing any named host-tool work" in normalized_instructions
    assert "verification helper does not finish the Turn while named host-tool work remains" in normalized_instructions
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


def test_runtime_without_host_tool_dispatch_stops_advertising_those_tools() -> None:
    """A Sandbox with no host-tool bridge must not be told to call unbound tools."""
    fragments = fleet_rlm_instruction_fragments(recursion_enabled=True, host_tool_dispatch=False)

    assert fragments.tools == TOOL_RLM_INSTRUCTIONS_NO_DISPATCH
    assert fragments.recursion is None
    composed = fragments.compose()
    # This overlay removes Fleet-provided tools only. DSPy adds its native
    # semantic tools outside this Signature instruction composition.
    for guidance in (
        "call ``fetch_url`` once",
        "Use ``rlm_query(capsule=capsule)``",
    ):
        assert guidance not in composed
    assert "Do not probe for" in composed
    assert "``llm_query``" not in composed


def test_host_tool_dispatch_still_emits_workspace_guidance_only_when_dispatched() -> None:
    """Workspace guidance shares the host-tool bridge, so it follows the same capability."""
    signature = FleetRLMSignature.with_instructions("base")
    dispatched = root_signature_for_recursion(
        signature,
        recursion_enabled=False,
        tool_names=frozenset({"read_workspace_text_batch"}),
        host_tool_dispatch=True,
    )
    undispatchable = root_signature_for_recursion(
        signature,
        recursion_enabled=False,
        tool_names=frozenset({"read_workspace_text_batch"}),
        host_tool_dispatch=False,
    )

    assert "read_workspace_text_batch" in dispatched.instructions
    assert "read_workspace_text_batch" not in undispatchable.instructions


def test_build_program_threads_host_tool_dispatch_into_the_signature() -> None:
    """The spec capability reaches the built program's instructions."""
    options = RLMOptions(max_iters=1, max_llm_calls=1)
    without = build_program(FleetProgramSpec(signature=FleetRLMSignature, options=options, host_tool_dispatch=False))
    with_dispatch = build_program(
        FleetProgramSpec(signature=FleetRLMSignature, options=options, host_tool_dispatch=True)
    )

    assert "Fleet recursion, URL-fetch, or Workspace host tool" in without.signature.instructions
    assert "Fleet recursion, URL-fetch, or Workspace host tool" not in with_dispatch.signature.instructions


def test_dspy_native_semantic_tools_survive_fleet_no_dispatch_overlay() -> None:
    """Fleet instruction filtering cannot remove DSPy's built-in semantic tools."""
    from dspy.predict.rlm import ACTION_INSTRUCTIONS_TEMPLATE

    options = RLMOptions(max_iters=1, max_llm_calls=1)
    rlm = build_program(FleetProgramSpec(signature=FleetRLMSignature, options=options, host_tool_dispatch=False))

    assert "``llm_query``" not in rlm.signature.instructions
    assert "llm_query(prompt)" in ACTION_INSTRUCTIONS_TEMPLATE
    assert "llm_query(prompt)" in rlm.generate_action.signature.instructions
    assert set(rlm._make_llm_tools()) == {"llm_query", "llm_query_batched"}
