"""Every DSPy signature Fleet uses, in one place.

GEPA tunes these instructions; keep field names stable. The route vocabulary, stopping
rules, and runtime input names come from `fleet_rlm.contracts`.
"""

from typing import Any

import dspy
from dspy import make_signature

from fleet_rlm.contracts import STOPPING_RULES, TRIAGE_INSTRUCTION, TRIAGE_ROUTE, make_choice_type

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
            dspy.OutputField(desc=TRIAGE_INSTRUCTION),
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
