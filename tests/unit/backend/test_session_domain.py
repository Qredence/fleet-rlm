"""Session and canonical Turn input domain contracts."""

from __future__ import annotations

from uuid import UUID

import pytest


def test_turn_input_preserves_text_and_has_a_stable_versioned_fingerprint() -> None:
    from fleet_rlm.sessions.models import TurnInput

    attachment_id = UUID("00000000-0000-0000-0000-000000000001")
    value = TurnInput(text="  inspect  ", attachment_ids=(attachment_id,))

    assert value.canonical_json == (
        '{"attachment_ids":["00000000-0000-0000-0000-000000000001"],"schema_version":1,"text":"  inspect  "}'
    )
    assert value.fingerprint == "51e05eeee8a53adbf45688f66640162ddd376fa78ab5b502c3216548d86f41d3"


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


def test_sequence_cursor_is_an_actual_nonnegative_sequence() -> None:
    from fleet_rlm.sessions.catalog import SequenceCursor

    assert SequenceCursor.from_query(None).after_sequence is None
    assert SequenceCursor.from_query(0).after_sequence == 0
    assert SequenceCursor.from_query(41).next_after_sequence(42) == 42

    with pytest.raises(ValueError):
        SequenceCursor.from_query(-1)
