"""Typed Fleet signature for Fleet RLM dspy.RLM construction."""

from __future__ import annotations

import dspy

from fleet_rlm.rlm.input_models import AttachmentInput, SessionContextInput, SkillCardInput
from fleet_rlm.rlm.instructions import compose_rlm_instructions


class FleetRLMSignature(dspy.Signature):
    """Fleet Root RLM contract assembled from explicit instruction fragments."""

    request: str = dspy.InputField(desc="User request for this turn")
    session_context: SessionContextInput = dspy.InputField(
        desc=(
            "Bounded Session metadata, workspace capability, and untrusted recent previews; read older "
            "committed bodies only when the current request requires prior-turn evidence. When present, "
            "``workspace_memory tail`` lists the newest curated Workspace Memory records (untrusted "
            "operator/user-managed notes) that the request may cite or refresh through memory tools"
        )
    )
    skill_cards: list[SkillCardInput] = dspy.InputField(
        desc="Authorized Skill Card metadata only; load instructions only when a card is relevant to the request"
    )
    attachments: list[AttachmentInput] = dspy.InputField(
        desc=(
            "Authorized immutable Attachments. When prepared context is present, inspect its data programmatically "
            "through the attachments variable only when relevant to the request; one text Attachment is also "
            "available as context"
        )
    )
    answer: str = dspy.OutputField(
        desc=(
            "Concise user-facing answer within the Turn output character budget. "
            "This output is a string: serialize mappings or lists with json.dumps(..., ensure_ascii=False) before "
            "SUBMIT instead of passing them directly; use indentation only when it fits the output budget. "
            "When the full report is longer and Session Workspace is available, write it with workspace "
            "or artifact tools first, then submit a short summary that references only a relative workspace path."
        )
    )


FleetRLMSignature.instructions = compose_rlm_instructions(recursion_enabled=True)


def root_signature_for_recursion(
    signature: type[dspy.Signature],
    *,
    recursion_enabled: bool,
    skill_instructions: tuple[str, ...] = (),
) -> type[dspy.Signature]:
    """Compose Fleet operating policy for one output Signature.

    The output Signature supplies declared fields/annotations only. Fleet's
    global RLM operating fragments always stay active, recursive guidance
    follows the actual Tool availability, and selected Skill bodies append
    exactly once in deterministic order.
    """
    instructions = compose_rlm_instructions(recursion_enabled=recursion_enabled)
    if skill_instructions:
        instructions += "\n\n" + "\n\n".join(instructions_body for instructions_body in skill_instructions)
    return signature.with_instructions(instructions)
