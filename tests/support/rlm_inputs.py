"""Bounded input metadata shared by unit and DSPy adapter contract suites."""

from uuid import UUID

import dspy

from fleet_rlm.attachments.models import PreparedAttachment
from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
from fleet_rlm.rlm.program import build_rlm_input_kwargs
from fleet_rlm.skills.models import SkillCard
from fleet_rlm.workspace.models import DAYTONA_WORKSPACE_CAPABILITY

SESSION_ID = UUID("00000000-0000-0000-0000-000000000001")

SKILL_ID = UUID("00000000-0000-0000-0000-000000000002")

ATTACHMENT_ID = UUID("00000000-0000-0000-0000-000000000003")


def _payload() -> dict[str, object]:
    return build_rlm_input_kwargs(
        request="Summarize the report",
        history=dspy.History(messages=[]),
        session_context=SessionContextManifest(
            session_id=SESSION_ID,
            checkpoint_version=7,
            message_count=3,
            recent=(TurnPreview(ordinal=3, role="user", preview="Recent request"),),
        ),
        skill_cards=(
            SkillCard(
                id=SKILL_ID,
                name="report-builder",
                description="Build bounded reports",
                version="1.0.0",
                resources_available=True,
            ),
        ),
        attachments=(
            PreparedAttachment(
                ATTACHMENT_ID,
                "report.md",
                "text/markdown",
                128,
                "a" * 64,
            ),
        ),
        workspace=DAYTONA_WORKSPACE_CAPABILITY,
    )
