from __future__ import annotations

import scripts.check_architecture_vocabulary as checker


def test_architecture_vocabulary_and_dspy_tool_ownership_are_frozen() -> None:
    assert checker.main() == 0
