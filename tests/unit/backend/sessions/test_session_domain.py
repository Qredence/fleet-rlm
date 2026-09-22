"""Session and canonical Turn input domain contracts."""

from __future__ import annotations

from uuid import UUID

import pytest

from fleet_rlm.skills.models import SkillSelectionRef


def test_turn_input_preserves_text_and_has_a_stable_versioned_fingerprint() -> None:
    from fleet_rlm.sessions.models import TurnInput

    attachment_id = UUID("00000000-0000-0000-0000-000000000001")
    skill_id = UUID("00000000-0000-0000-0000-000000000002")
    value = TurnInput(
        text="  inspect  ",
        attachment_ids=(attachment_id,),
        skill_selections=(SkillSelectionRef(skill_id, "2.0.0"),),
    )

    assert value.canonical_json == (
        '{"attachment_ids":["00000000-0000-0000-0000-000000000001"],"schema_version":2,'
        '"skill_selections":[{"expected_version":"2.0.0","id":"00000000-0000-0000-0000-000000000002"}],'
        '"text":"  inspect  "}'
    )
    assert value.fingerprint == "3764042e0539d2a63f558174332d98d06908f971df2ac7dbbd8faf76b04670ef"


def test_turn_input_codec_reads_v1_rows_from_the_canonical_baseline() -> None:
    from fleet_rlm.sessions.models import TurnInput, TurnInputCodec

    skill_id = UUID("00000000-0000-0000-0000-000000000002")
    current = TurnInput("inspect", skill_selections=(SkillSelectionRef(skill_id, "2.0.0"),))

    assert TurnInputCodec.decode(TurnInputCodec.encode(current)) == current
    assert TurnInputCodec.decode(
        {
            "schema_version": 1,
            "text": "legacy",
            "attachment_ids": [],
        }
    ) == TurnInput("legacy")


def test_turn_input_fingerprint_includes_version_pinned_skill_selections() -> None:
    from fleet_rlm.sessions.models import TurnInput

    skill_id = UUID("00000000-0000-0000-0000-000000000002")

    without_skill = TurnInput("inspect")
    with_v1 = TurnInput("inspect", skill_selections=(SkillSelectionRef(skill_id, "1.0.0"),))
    with_v2 = TurnInput("inspect", skill_selections=(SkillSelectionRef(skill_id, "2.0.0"),))

    assert len({without_skill.fingerprint, with_v1.fingerprint, with_v2.fingerprint}) == 3


def test_empty_v2_input_replays_v1_canonical_baseline_fingerprint() -> None:
    from fleet_rlm.sessions.models import TurnInput

    value = TurnInput("inspect")
    assert value.fingerprint in value.acceptable_fingerprints
    assert len(value.acceptable_fingerprints) == 2
    assert TurnInput("inspect", skill_selections=(SkillSelectionRef(UUID(int=2), "1.0.0"),)).fingerprint not in (
        value.acceptable_fingerprints
    )


@pytest.mark.parametrize(
    ("text", "attachment_ids"),
    [
        ("   ", ()),
        ("x", (UUID(int=1), UUID(int=1))),
        ("x", tuple(UUID(int=index) for index in range(1, 34))),
    ],
)
def test_turn_input_rejects_blank_duplicate_or_oversized_input(
    text: str,
    attachment_ids: tuple[UUID, ...],
) -> None:
    from fleet_rlm.sessions.models import TurnInput, TurnInputValidationError

    with pytest.raises(TurnInputValidationError):
        TurnInput(text=text, attachment_ids=attachment_ids)


def test_turn_input_rejects_duplicate_or_oversized_skill_selections() -> None:
    from fleet_rlm.sessions.models import TurnInput, TurnInputValidationError

    selection = SkillSelectionRef(UUID(int=1), "1.0.0")

    with pytest.raises(TurnInputValidationError):
        TurnInput("inspect", skill_selections=(selection, selection))

    with pytest.raises(TurnInputValidationError):
        TurnInput(
            "inspect",
            skill_selections=tuple(SkillSelectionRef(UUID(int=index), "1.0.0") for index in range(1, 6)),
        )


def test_sequence_cursor_is_an_actual_nonnegative_sequence() -> None:
    from fleet_rlm.sessions.catalog import SequenceCursor

    assert SequenceCursor().after_sequence is None
    assert SequenceCursor(after_sequence=0).after_sequence == 0
    assert SequenceCursor(after_sequence=41).next_after_sequence(42) == 42

    with pytest.raises(ValueError):
        SequenceCursor(after_sequence=-1)
