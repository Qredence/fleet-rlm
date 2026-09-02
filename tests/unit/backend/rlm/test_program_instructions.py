"""Root instruction-fragment composition contracts."""

from fleet_rlm.rlm.program import (
    BASE_RLM_INSTRUCTIONS,
    DISCOVERY_RLM_INSTRUCTIONS,
    RECURSION_RLM_INSTRUCTIONS,
    REPL_RLM_INSTRUCTIONS,
    TOOL_RLM_INSTRUCTIONS,
    FleetRLMSignature,
    compose_rlm_instructions,
    fleet_rlm_instruction_fragments,
    root_signature_for_recursion,
)


def test_default_fleet_signature_uses_composed_recursive_fragments() -> None:
    fragments = fleet_rlm_instruction_fragments(recursion_enabled=True)

    assert fragments.base == BASE_RLM_INSTRUCTIONS
    assert fragments.repl == REPL_RLM_INSTRUCTIONS
    assert fragments.tools == TOOL_RLM_INSTRUCTIONS
    assert fragments.recursion == RECURSION_RLM_INSTRUCTIONS
    assert FleetRLMSignature.instructions == fragments.compose()
    assert "rlm_query(prompt=prompt)" in FleetRLMSignature.instructions
    assert "6. Verify within the same action" in FleetRLMSignature.instructions


def test_nonrecursive_root_signature_omits_only_the_optional_recursion_fragment() -> None:
    recursive = FleetRLMSignature.instructions
    nonrecursive = root_signature_for_recursion(FleetRLMSignature, recursion_enabled=False).instructions

    assert "rlm_query(prompt=prompt)" not in nonrecursive
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
    assert "rlm_query(prompt=prompt)" in recursive.instructions
    assert "rlm_query(prompt=prompt)" not in nonrecursive.instructions


def test_fragment_composition_preserves_established_instruction_text() -> None:
    enabled = compose_rlm_instructions(recursion_enabled=True)
    disabled = compose_rlm_instructions(recursion_enabled=False)
    assert enabled.startswith(BASE_RLM_INSTRUCTIONS)
    assert REPL_RLM_INSTRUCTIONS in enabled
    assert TOOL_RLM_INSTRUCTIONS in enabled
    assert RECURSION_RLM_INSTRUCTIONS in enabled
    assert RECURSION_RLM_INSTRUCTIONS not in disabled
    assert DISCOVERY_RLM_INSTRUCTIONS in disabled
