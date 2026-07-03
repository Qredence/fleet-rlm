"""C3: broker circuit breaker reliability slice."""

from __future__ import annotations

import time

import pytest

from fleet_rlm.integrations.daytona.bridge import BrokerCircuitBreaker


def test_breaker_starts_closed() -> None:
    cb = BrokerCircuitBreaker(threshold=5, cooldown_seconds=10.0)
    assert cb.state == "closed"
    # No raise on check when closed.
    cb.raise_if_open()


def test_breaker_trips_after_threshold() -> None:
    cb = BrokerCircuitBreaker(threshold=3, cooldown_seconds=30.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "open"
    from dspy.primitives import CodeInterpreterError

    with pytest.raises(CodeInterpreterError, match="circuit breaker tripped"):
        cb.raise_if_open()


def test_breaker_success_resets() -> None:
    cb = BrokerCircuitBreaker(threshold=3, cooldown_seconds=30.0)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.state == "closed"
    # Failures counter reset; need 3 more to trip.
    cb.record_failure()
    cb.raise_if_open()  # still closed


def test_breaker_half_open_after_cooldown() -> None:
    """After cooldown, the breaker goes half-open and allows one probe."""
    cb = BrokerCircuitBreaker(threshold=2, cooldown_seconds=0.01)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    # Wait past cooldown.
    time.sleep(0.02)
    cb.raise_if_open()  # transitions to half_open, no raise
    assert cb.state == "half_open"
    # A success closes the circuit.
    cb.record_success()
    assert cb.state == "closed"


def test_breaker_half_open_failure_reopens() -> None:
    cb = BrokerCircuitBreaker(threshold=2, cooldown_seconds=0.01)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.02)
    cb.raise_if_open()  # half_open
    # Probe fails -> reopen.
    cb.record_failure()
    assert cb.state == "open"
    from dspy.primitives import CodeInterpreterError

    with pytest.raises(CodeInterpreterError):
        cb.raise_if_open()


def test_breaker_rejects_invalid_config() -> None:
    with pytest.raises(ValueError):
        BrokerCircuitBreaker(threshold=0)
    with pytest.raises(ValueError):
        BrokerCircuitBreaker(cooldown_seconds=-1)


def test_breaker_threshold_minus_one_does_not_trip() -> None:
    cb = BrokerCircuitBreaker(threshold=5, cooldown_seconds=30.0)
    for _ in range(4):
        cb.record_failure()
    assert cb.state == "closed"
    cb.raise_if_open()  # no raise, still closed
