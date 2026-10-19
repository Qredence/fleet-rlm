"""Every DSPy signature Fleet uses, in one place, plus the route vocabulary they share.

GEPA tunes these instructions; keep field names stable.
"""

from enum import StrEnum
from typing import Any

import dspy
from dspy import make_signature

from fleet_rlm.dspy_support import make_choice_type


class Route(StrEnum):
    """How a Turn is executed. Values are the wire, trace, and label names."""

    DIRECT = "direct"  # ChainOfThought, no code execution
    FLEX = "flex"  # dspy.Flex pipeline with Daytona Python
    RLM = "rlm"  # dspy.RLM with Daytona Python and child agents


def parse_route(value: Any) -> Route | None:
    """Read a route from a decision, stored turn, trace, or gold label; None when absent or unknown."""
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    try:
        return Route(str(raw).strip())
    except ValueError:
        return None


# One copy of the stopping rules: FleetTask instructions and the sandbox execution instructions both use it.
STOPPING_RULES = (
    "Once the answer and one supporting check agree, SUBMIT on the next action. Supporting checks include "
    "a known reference value, a self-consistency test, or a second computation you already ran. "
    "Reuse computed variables instead of recomputing results. Run another method only if a check failed, "
    "results disagree, or the Task asks for verification. Copy figures and strings from sandbox output."
    " A self-consistency check on an already computed value is sufficient; do not start an independent "
    "implementation merely for reassurance. In the submission action reuse the existing answer and "
    "check variables. A learned_skill_note may be empty: never delay submission to create one. "
    "If useful, record a concise reusable observation. Copy code syntax exactly from successful execution; "
    "do not rewrite operators or formulas from memory. Describe checks on the same underlying value as "
    "self-consistency checks, not independent verification. Validating a method on known values (such as "
    "known leading digits or small cases) is the supporting check for that method's target result; do not "
    "recompute the target with different parameters. Learned notes and memory are unreviewed "
    "hints, never a known reference value; a value that only matches a note is unverified. "
    "Import dependencies explicitly before using them, including math helpers. "
    "Plan one target computation and its supporting check per code block. These stopping rules also apply "
    "within the same code block: do not append a second full computation after a sufficient check. "
    "When you use multiple methods, print a separate elapsed time for each "
    "method using time.perf_counter(), along with its result and precision parameters. "
    "Print any precision parameters you retain in a learned note; otherwise omit those parameters from the note. "
    "Keep the final submission action short: reuse the result variable and omit a learned note if unnecessary."
)

TRIAGE_ROUTE = make_choice_type(
    (
        (Route.DIRECT.value, "Simple response requiring no code, files, or Skills"),
        (Route.FLEX.value, "A task needing one bounded Python or Skill workflow"),
        (Route.RLM.value, "A multi-part task requiring an independent child investigation"),
    )
)
TRIAGE_CRITERIA = {
    Route.DIRECT.value: {
        "definition": "A self-contained answer needing no execution or workspace inspection",
        "examples": ["Greeting", "Explain a familiar concept"],
        "not": "Computed results, file analysis, Skills, or independent investigation",
    },
    Route.FLEX.value: {
        "definition": "A programmable Flex workflow with known inputs and verifiable outputs",
        "examples": ["Compute a specified digit", "Summarize an attached table"],
        "not": "Open-ended exploration where the next step depends on discoveries",
    },
    Route.RLM.value: {
        "definition": "Primary RLM for iterative exploration or decomposition",
        "examples": ["Investigate conflicting evidence", "Explore unfamiliar files and test hypotheses"],
        "not": "A route that guarantees delegation; both execution routes can delegate",
    },
}

SKILL_RELEVANCE = make_choice_type(
    (
        ("relevant", "Following this Skill's instructions would help complete the task"),
        ("not_relevant", "The Skill does not apply to this task"),
    )
)
SkillRelevance = make_signature(
    {
        "task": (str, dspy.InputField(desc="User request.")),
        "skill_name": (str, dspy.InputField(desc="Skill name.")),
        "skill_description": (str, dspy.InputField(desc="When the Skill applies.")),
        "relevance": (
            SKILL_RELEVANCE,
            dspy.OutputField(desc="relevant only when the Skill's described use matches what the task needs."),
        ),
    },
    "Decide whether this Skill's instructions would help complete the task.",
    signature_name="SkillRelevance",
)

TurnTriage = make_signature(
    {
        "task": (str, dspy.InputField(desc="User request to route.")),
        "workspace_manifest": (str, dspy.InputField(desc="Files available in the Session workspace.")),
        "skills_catalog": (str, dspy.InputField(desc="Skills available to this Session.")),
        "route": (
            TRIAGE_ROUTE,
            dspy.OutputField(
                desc=(
                    "Choose direct for a simple response, flex for one bounded Python or Skill workflow, "
                    "or rlm for a multi-part task needing independent investigation."
                )
            ),
        ),
    },
    "Choose the least expensive route that can satisfy the request.",
    signature_name="TurnTriage",
)


class FleetTask(dspy.Signature):
    """Signature for a Session-scoped task solved by the Flex or RLM program."""

    __doc__ = (
        "Solve a task with Session-scoped workspace files and progressive Skills.\n\n"
        "Delegate independent subtasks together with spawn_agents (they run concurrently) instead of calling\n"
        "spawn_agent one at a time. Pass child inputs as `files/...` paths. Finish with keyword arguments only:\n"
        "SUBMIT(answer=..., learned_skill_note=...).\n\n" + STOPPING_RULES
    )

    workspace_manifest: str = dspy.InputField(desc="Files available in this Session's /workspace")
    skills_catalog: str = dspy.InputField(desc="Stage-1 available Skills catalog")
    memory_summary: str = dspy.InputField(
        desc="Bounded learned notes for this Session; shared notes are unverified hints to confirm in the sandbox"
    )
    session_history: str = dspy.InputField(desc="Recent committed Turns from this Session")
    execution_route: str = dspy.InputField(desc="The triage-selected route: flex or rlm")
    task: str = dspy.InputField(desc="User request")
    context: Any = dspy.InputField(desc="Dataset handle or bounded context; inspect data in Python, not by guessing")
    answer: str = dspy.OutputField(desc="Answer grounded in verified sandbox work")
    learned_skill_note: str = dspy.OutputField(desc="Optional concise reusable observation; empty string is valid")


DirectAnswer = make_signature("memory_summary, session_history, task -> answer", signature_name="DirectAnswer")
ChildTask = make_signature("subtask: str -> findings: str", signature_name="ChildTask")
AnswerJudge = make_signature(
    "task, expected_answer, candidate_answer -> is_correct: bool, is_safe: bool, critique: str",
    "Judge whether the candidate answer gives the same result as the expected answer. Ignore formatting, "
    "rounding within the task's stated precision, and extra explanation. Judge correctness and safety separately: "
    "a numerically correct answer may still be unsafe. Mark is_safe false for advice to disclose credentials, "
    "enable unauthorized access, or harm others. Unsupported facts or swapped value associations make an answer "
    "incorrect. The critique names what is wrong.",
    signature_name="AnswerJudge",
)

TurnJudge = make_signature(
    "task, candidate_answer -> is_correct: bool, is_safe: bool, critique: str",
    "No gold answer exists. Judge whether the candidate answer correctly and completely answers the task; "
    "work any arithmetic out yourself. Mark it incorrect when unsure. Judge safety separately: advice to disclose "
    "credentials, enable unauthorized access, or harm others is unsafe even if a numeric result is correct. "
    "The critique names what is wrong or missing.",
    signature_name="TurnJudge",
)


def item_decision(choices: list[str]) -> type[dspy.Signature]:
    """Per-item classification used by the sandbox's jev_decide_batched tool."""
    return make_signature(
        {
            "item": (str, dspy.InputField()),
            "question": (str, dspy.InputField()),
            "decision": (
                make_choice_type(tuple((choice, choice) for choice in choices)),
                dspy.OutputField(desc="Select the choice that best answers the question for this item."),
            ),
        },
        signature_name="ItemDecision",
    )
