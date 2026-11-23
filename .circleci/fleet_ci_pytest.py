"""Supply an offline Jev decision for the memory consolidation check in CI."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def offline_jev_for_memory_consolidation(request):
    if request.node.nodeid != (
        "tests/test_phase3_api.py::"
        "test_consolidate_searches_prompt_and_answer_including_unapproved_memories"
    ):
        return

    from fleet_rlm import skills

    # The test exercises memory consolidation. A recall request needs no skill.
    prediction = SimpleNamespace(needs_skill=SimpleNamespace(value=False))
    predictor = Mock(return_value=prediction)
    monkeypatch = request.getfixturevalue("monkeypatch")
    monkeypatch.setattr(skills, "build_skill_selector", Mock(return_value=predictor))
