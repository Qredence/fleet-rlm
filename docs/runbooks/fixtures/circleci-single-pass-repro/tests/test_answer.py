"""Exercise the minimal source module for CircleCI impact analysis."""

from sample import answer


def test_answer() -> None:
    """Check the fixture answer."""
    assert answer() == 42
