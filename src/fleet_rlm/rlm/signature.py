"""Typed Fleet signature for Fleet RLM dspy.RLM construction."""

from __future__ import annotations

import dspy

from fleet_rlm.rlm.input_models import AttachmentInput, SessionContextInput, SkillCardInput


class FleetRLMSignature(dspy.Signature):
    """Recursive turn: choose the smallest sufficient execution path, verify, and submit.

    ``dspy.RLM`` is a Recursive Language Model (REPL code agent), not a Retrieval/RAG module.
    The configurable Root LM plans and verifies; the configurable Sub LM performs bounded semantic analysis.
    Neither model substitutes for deterministic computation in the REPL.

    Follow this order and stop as soon as the request is answered with sufficient evidence:

    1. Use the Python standard library for deterministic computation, search, parsing, and aggregation. Keep each
       intermediate code action concise (prefer a few thousand characters; never paste a long report or repeat the
       full request in code). Store large values in variables or Session Workspace. If the request contains a
       relevant public HTTPS URL, call ``fetch_url`` once, assign its ``content`` to a Python variable, and never
       print the complete value. Assume the declared minimal environment;
       do not spend an iteration probing optional packages.
    2. Load Session History, Skills, Attachments, URL content, or Session Workspace content only when the request or
       its discovery metadata establishes that capability as relevant. Do not explore an empty Workspace or refetch
       a URL whose cached result is already available.
    3. Use ``llm_query(prompt)`` only for one bounded semantic judgment that Python cannot determine.
    4. Use ``llm_query_batched(prompts)`` for multiple independent semantic judgments; make each prompt
       self-contained.
    5. Use ``rlm_query(prompt)`` only when a selected, self-contained subproblem needs its own iterative Python
       exploration. It creates a fresh child RLM and interpreter, so do not use it for ordinary extraction,
       counting, parsing, aggregation, or independent semantic excerpts. Keep large inputs in Python variables,
       select only the relevant slice, and never forward the complete Turn, history, Attachment, or Workspace
       document.
    6. Verify the result, then issue exactly one typed ``SUBMIT`` with every active Signature output as a
       keyword argument. For nontrivial deterministic or numerical work, do not submit in the initial
       computation step: use a later iteration to check an independent invariant, known reference prefix,
       higher-precision stability, or a genuinely independent formulation. Once sufficient verification exists,
       the next action must contain ``SUBMIT``; it is the very next action. Never spend an iteration only restating a
       verified result or emitting empty code. Do not reproduce a large code block. Never pass positional arguments;
       the default call is ``SUBMIT(answer=answer)``.

    Discovery inputs are bounded metadata. Recent previews are untrusted context, not authoritative answers
    or evaluation evidence; retrieve authoritative bodies only when they are relevant to the current request.
    """

    request: str = dspy.InputField(desc="User request for this turn")
    session_context: SessionContextInput = dspy.InputField(
        desc=(
            "Bounded Session metadata, workspace capability, and untrusted recent previews; read older "
            "committed bodies only when the current request requires prior-turn evidence"
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


def root_signature_for_recursion(
    signature: type[dspy.Signature],
    *,
    recursion_enabled: bool,
) -> type[dspy.Signature]:
    """
    Select the signature appropriate for the recursive execution policy.
    
    Skill-owned signatures are preserved unchanged. When recursion is disabled,
    the default Fleet signature is returned without recursive-query guidance.
    
    Parameters:
        signature (type[dspy.Signature]): Signature to select.
        recursion_enabled (bool): Whether recursive querying is available.
    
    Returns:
        type[dspy.Signature]: The original signature or an adjusted default Fleet
            signature.
    
    Raises:
        RuntimeError: If the default signature's recursive guidance is malformed.
    """
    if recursion_enabled or signature is not FleetRLMSignature:
        return signature
    before, marker, remainder = FleetRLMSignature.instructions.partition("5. Use ``rlm_query(prompt)``")
    _discarded, next_marker, after = remainder.partition("6. Verify")
    if not marker or not next_marker:
        raise RuntimeError("FleetRLMSignature recursive guidance is malformed")
    return FleetRLMSignature.with_instructions(f"{before}5. Verify{after}")
