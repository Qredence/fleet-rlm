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
            "When the full report is longer and Session Workspace is available, write it with workspace "
            "or artifact tools first, then submit a short summary that references only a relative workspace path."
        )
    )


FleetRLMSignature.instructions = compose_rlm_instructions(recursion_enabled=True)


def root_signature_for_recursion(
    signature: type[dspy.Signature],
    *,
    recursion_enabled: bool,
) -> type[dspy.Signature]:
    """
    Select the signature appropriate for the recursive execution policy.

    Skill-owned signatures are preserved unchanged. The default Fleet signature
    is recomposed from explicit fragments; recursive guidance is included only
    when recursive querying is available.

    Parameters:
        signature (type[dspy.Signature]): Signature to select.
        recursion_enabled (bool): Whether recursive querying is available.

    Returns:
        type[dspy.Signature]: The original signature or an adjusted default Fleet
            signature.

    """
    if signature is not FleetRLMSignature:
        return signature
    return FleetRLMSignature.with_instructions(compose_rlm_instructions(recursion_enabled=recursion_enabled))
