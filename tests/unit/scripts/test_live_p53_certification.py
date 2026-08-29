"""Parser contract tests for the serial P53 live certification runner."""

from __future__ import annotations

from scripts.live_p53_certification import _pytest_exactly_one_passed


def test_accepts_single_pass_under_sixty_seconds() -> None:
    output = ".                                                        [100%]\n1 passed in 12.34s\n"
    assert _pytest_exactly_one_passed(output) is True


def test_accepts_single_pass_with_human_readable_duration_suffix() -> None:
    # pytest format_session_duration appends " (H:MM:SS)" for sessions >= 60s;
    # live Daytona lanes always cross that threshold.
    output = ".                                                        [100%]\n1 passed in 146.05s (0:02:26)\n"
    assert _pytest_exactly_one_passed(output) is True


def test_accepts_single_pass_with_warnings() -> None:
    output = ".                                            [100%]\n1 passed, 2 warnings in 61.00s (0:01:01)\n"
    assert _pytest_exactly_one_passed(output) is True


def test_rejects_double_quiet_output_without_summary() -> None:
    # Effective -qq (runner -q plus pyproject addopts -q) suppresses the
    # terminal summary entirely; fail closed instead of trusting the dots.
    output = ".                                                        [100%]\n"
    assert _pytest_exactly_one_passed(output) is False


def test_rejects_multiple_passed() -> None:
    assert _pytest_exactly_one_passed("..\n2 passed in 1.00s\n") is False


def test_rejects_failure_counts() -> None:
    assert _pytest_exactly_one_passed("F.\n1 passed, 1 failed in 1.00s\n") is False


def test_rejects_skip_only() -> None:
    assert _pytest_exactly_one_passed("s\n1 skipped in 0.50s\n") is False


def test_uses_last_summary_line() -> None:
    output = "1 failed in 0.10s\n.\n1 passed in 70.00s (0:01:10)\n"
    assert _pytest_exactly_one_passed(output) is True
