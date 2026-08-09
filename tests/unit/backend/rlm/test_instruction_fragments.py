"""Root instruction-fragment composition contracts."""

from fleet_rlm.rlm.instructions import (
    BASE_RLM_INSTRUCTIONS,
    DISCOVERY_RLM_INSTRUCTIONS,
    RECURSION_RLM_INSTRUCTIONS,
    REPL_RLM_INSTRUCTIONS,
    TOOL_RLM_INSTRUCTIONS,
    compose_rlm_instructions,
    fleet_rlm_instruction_fragments,
)
from fleet_rlm.rlm.signature import FleetRLMSignature, root_signature_for_recursion


def test_default_fleet_signature_uses_composed_recursive_fragments() -> None:
    fragments = fleet_rlm_instruction_fragments(recursion_enabled=True)

    assert fragments.base == BASE_RLM_INSTRUCTIONS
    assert fragments.repl == REPL_RLM_INSTRUCTIONS
    assert fragments.tools == TOOL_RLM_INSTRUCTIONS
    assert fragments.recursion == RECURSION_RLM_INSTRUCTIONS
    assert FleetRLMSignature.instructions == fragments.compose()
    assert "rlm_query(prompt=prompt)" in FleetRLMSignature.instructions
    assert "6. Verify the result" in FleetRLMSignature.instructions


def test_nonrecursive_root_signature_omits_only_the_optional_recursion_fragment() -> None:
    recursive = FleetRLMSignature.instructions
    nonrecursive = root_signature_for_recursion(FleetRLMSignature, recursion_enabled=False).instructions

    assert "rlm_query(prompt=prompt)" not in nonrecursive
    assert "5. Verify the result" in nonrecursive
    assert RECURSION_RLM_INSTRUCTIONS in recursive
    assert RECURSION_RLM_INSTRUCTIONS not in nonrecursive
    assert nonrecursive.endswith(DISCOVERY_RLM_INSTRUCTIONS)


def test_skill_owned_signature_is_never_rewritten_by_fragment_composition() -> None:
    import dspy

    class CustomResult(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    assert root_signature_for_recursion(CustomResult, recursion_enabled=True) is CustomResult
    assert root_signature_for_recursion(CustomResult, recursion_enabled=False) is CustomResult


def test_fragment_composition_preserves_established_instruction_text() -> None:
    enabled = compose_rlm_instructions(recursion_enabled=True)
    disabled = compose_rlm_instructions(recursion_enabled=False)
    assert enabled.startswith(BASE_RLM_INSTRUCTIONS)
    assert REPL_RLM_INSTRUCTIONS in enabled
    assert TOOL_RLM_INSTRUCTIONS in enabled
    assert RECURSION_RLM_INSTRUCTIONS in enabled
    assert RECURSION_RLM_INSTRUCTIONS not in disabled
    assert DISCOVERY_RLM_INSTRUCTIONS in disabled
