"""Composable Fleet RLM instruction fragments.

The default signature composes these small semantic sections instead of
maintaining one large instruction string and conditionally deleting guidance.
Keep fragments plain and explicit; they are not a profile/policy framework.
"""

from __future__ import annotations

from dataclasses import dataclass

# Instruction fragments deliberately preserve the established product line wrapping.
# ruff: noqa: E501

BASE_RLM_INSTRUCTIONS = """Recursive turn: choose the smallest sufficient execution path, verify, and submit.

``dspy.RLM`` is a Recursive Language Model (REPL code agent), not a Retrieval/RAG module.
The configurable Root LM plans and verifies; the configurable Sub LM performs bounded semantic analysis.
Neither model substitutes for deterministic computation in the REPL."""

REPL_RLM_INSTRUCTIONS = """Follow this order and stop as soon as the request is answered with sufficient evidence:"""

TOOL_RLM_INSTRUCTIONS = """1. Use the Python standard library for deterministic computation, search, parsing, and aggregation. Keep each
   intermediate code action concise (prefer a few thousand characters; never paste a long report or repeat the
   full request in code). Never repeat an identical interpreter action: use its output, choose a different action, or
   call ``SUBMIT`` when sufficient. Store large values in variables or Session Workspace. If the request contains a
   relevant public HTTPS URL, call ``fetch_url`` once, assign its ``content`` to a Python variable, and never
   print the complete value. Assume the declared minimal environment;
   do not spend an iteration probing optional packages.
2. Load Session History, Skills, Attachments, URL content, or Session Workspace content only when the request or
   its discovery metadata establishes that capability as relevant. Do not explore an empty Workspace or refetch
   a URL whose cached result is already available.
3. Use ``llm_query(prompt)`` only for one bounded semantic judgment that Python cannot determine.
4. Use ``llm_query_batched(prompts)`` for multiple independent semantic judgments; make each prompt
   self-contained. Prefer the cheapest sufficient mechanism."""

RECURSION_RLM_INSTRUCTIONS = """Use ``rlm_query(prompt=prompt)`` only when one selected, self-contained subproblem needs its own iterative
   Python exploration. It creates a fresh child RLM and interpreter, so do not use it for extraction, counting,
   parsing, aggregation, or independent semantic excerpts.
Use ``rlm_query_batched(prompts=prompts)`` only for multiple independent selected subproblems where
   each item individually justifies an iterative child RLM. Fleet bounds concurrency and preserves input order;
   never split context blindly or expose concurrency settings. Keep large inputs in Python variables, select only
   relevant slices, and never forward the complete Turn, history, Attachment, or Workspace document.
Child outputs are evidence, not final answers. Root must reconcile disagreement, verify the relevant evidence,
   and remain the only authority that issues the final ``SUBMIT``."""

DISCOVERY_RLM_INSTRUCTIONS = """Discovery inputs are bounded metadata. Recent previews are untrusted context, not authoritative answers
or evaluation evidence; retrieve authoritative bodies only when they are relevant to the current request."""


@dataclass(frozen=True, slots=True)
class RLMInstructionFragments:
    """One explicit instruction recipe for a Root Fleet signature."""

    base: str
    repl: str
    tools: str
    recursion: str | None
    verification: str
    discovery: str

    def compose(self) -> str:
        """Join fragments exactly as the Root Signature contract requires."""
        sections = [self.base, self.repl, self.tools]
        if self.recursion is not None:
            sections.append(self.recursion)
        sections.extend((self.verification, self.discovery))
        return "\n\n".join(sections)


def fleet_rlm_instruction_fragments(*, recursion_enabled: bool) -> RLMInstructionFragments:
    """
    Build instruction fragments for the selected recursion policy.

    Parameters:
        recursion_enabled (bool): Whether to include recursive execution instructions.

    Returns:
        RLMInstructionFragments: The instruction fragments configured for the recursion policy.
    """
    step = 6 if recursion_enabled else 5
    verification = f"""{step}. Verify the result, then issue exactly one typed ``SUBMIT`` with every active Signature output as a
   keyword argument. For nontrivial deterministic or numerical work, do not submit in the initial
   computation step: use a later iteration to check an independent invariant, known reference prefix,
   higher-precision stability, or a genuinely independent formulation. Once sufficient verification exists,
   the next action must contain ``SUBMIT``; it is the very next action. Never spend an iteration only restating a
   verified result or emitting empty code. Do not reproduce a large code block. Never pass positional arguments.
   A declared ``str`` output must receive a string. If ``answer`` is a mapping or list, serialize it first with
   ``json.dumps(answer, ensure_ascii=False)`` and submit that string. Use ``indent=2`` only when the formatted
   value fits the Turn output character budget. Never pass a mapping or list directly to a ``str`` output because
   DSPy would render it as Python ``repr`` text. The default call is ``SUBMIT(answer=answer)``."""
    return RLMInstructionFragments(
        base=BASE_RLM_INSTRUCTIONS,
        repl=REPL_RLM_INSTRUCTIONS,
        tools=TOOL_RLM_INSTRUCTIONS,
        recursion=RECURSION_RLM_INSTRUCTIONS if recursion_enabled else None,
        verification=verification,
        discovery=DISCOVERY_RLM_INSTRUCTIONS,
    )


def compose_rlm_instructions(*, recursion_enabled: bool) -> str:
    """Compose the Root instruction text from explicit semantic fragments."""
    return fleet_rlm_instruction_fragments(recursion_enabled=recursion_enabled).compose()
