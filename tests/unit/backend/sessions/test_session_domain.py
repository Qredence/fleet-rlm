"""Session and canonical Turn input domain contracts."""

from __future__ import annotations

from uuid import UUID

import pytest

from fleet_rlm.skills.models import SkillSelectionRef


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
