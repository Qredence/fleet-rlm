"""Behavior contracts for program instructions."""

from __future__ import annotations

from fleet_rlm.rlm.program import (
    TOOL_RLM_INSTRUCTIONS,
    WORKSPACE_MUTATION_RLM_INSTRUCTIONS,
    FleetRLMSignature,
    RLMOptions,
    root_signature_for_recursion,
)
from fleet_rlm.workspace.models import DAYTONA_WORKSPACE_CAPABILITY, UNAVAILABLE_WORKSPACE_CAPABILITY
from tests.support.native_rlm import build_native_rlm_for_test


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
    assert "Files outside the mounted ``/workspace`` are not Session Workspace" in present


def test_tool_instructions_require_sandbox_research_and_bounded_precision() -> None:
    assert "SHA-256" in TOOL_RLM_INSTRUCTIONS
    assert "sys.executable -m pip" in TOOL_RLM_INSTRUCTIONS
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
    assert "Estimate the verification cost against the remaining action budget" in normalized_instructions
    assert "do not recompute a large result with a slower independent algorithm" in normalized_instructions
    assert "If existing checks are sufficient, ``SUBMIT`` in the next action" in normalized_instructions
    assert "state the uncertainty instead of starting an unbounded verification" in normalized_instructions
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


def test_workspace_capability_declares_temporary_durable_and_commit_gated_state() -> None:
    daytona = DAYTONA_WORKSPACE_CAPABILITY.instructions
    unavailable = UNAVAILABLE_WORKSPACE_CAPABILITY.instructions

    for marker in ("REPL variables", "sandbox-local files", "immediately durable", "Turn Commit"):
        assert marker in daytona
    assert "unavailable" in unavailable
    assert "REPL variables" in unavailable


def test_native_builder_threads_host_tool_dispatch_into_the_signature() -> None:
    options = RLMOptions(max_iters=1, max_llm_calls=1)
    without = build_native_rlm_for_test(signature=FleetRLMSignature, options=options, host_tool_dispatch=False)
    with_dispatch = build_native_rlm_for_test(signature=FleetRLMSignature, options=options, host_tool_dispatch=True)

    assert "Fleet recursion or Workspace host tool" in without.signature.instructions
    assert "Fleet recursion or Workspace host tool" not in with_dispatch.signature.instructions


def test_nondefault_observation_budget_preserves_the_original_signature() -> None:
    program = build_native_rlm_for_test(
        signature=FleetRLMSignature,
        options=RLMOptions(max_iters=1, max_llm_calls=1, max_output_chars=6_000),
    )

    assert program.signature is FleetRLMSignature
