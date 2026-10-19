"""Temporary failure used only on the unmerged activation acceptance branch."""


def test_circleci_quality_gate_blocks_failure():
    raise AssertionError("Intentional CircleCI activation acceptance failure")
