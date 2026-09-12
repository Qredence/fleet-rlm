"""Role HTTP timeout stays a ceiling after Turn binding and retries."""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

import pytest
from dspy.utils.exceptions import LMTimeoutError

from fleet_rlm.rlm.program import RLMModelBundle


class _DropTimeoutOnCopyLM:
    """Provider double whose copy() drops the role timeout the way a broken clone would."""

    def __init__(self, timeout: float, *, num_retries: int = 1) -> None:
        self.kwargs: dict[str, object] = {"timeout": timeout}
        self.num_retries = num_retries
        self.calls: list[dict[str, object]] = []
        self.history: list[object] = []

    def copy(self, **kwargs: object) -> _DropTimeoutOnCopyLM:
        copied = _DropTimeoutOnCopyLM(timeout=float(self.kwargs["timeout"]), num_retries=self.num_retries)
        copied.kwargs = {}
        copied.num_retries = int(kwargs.get("num_retries", 0))
        return copied

    def forward(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return object()

    async def aforward(self, **kwargs: object) -> object:
        return self.forward(**kwargs)


class _TimeoutThenRetryLM(_DropTimeoutOnCopyLM):
    """Consume the whole attempt timeout, then raise a retryable provider timeout."""

    def __init__(self, timeout: float, clock: list[float], *, num_retries: int = 1) -> None:
        super().__init__(timeout, num_retries=num_retries)
        self.clock = clock

    def copy(self, **kwargs: object) -> _TimeoutThenRetryLM:
        copied = _TimeoutThenRetryLM(float(self.kwargs["timeout"]), self.clock, num_retries=self.num_retries)
        copied.kwargs = {}
        copied.num_retries = int(kwargs.get("num_retries", 0))
        return copied

    def forward(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        timeout = kwargs.get("timeout")
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
            self.clock[0] += float(timeout)
        raise LMTimeoutError("provider timed out")


def test_sub_role_timeout_is_a_hard_ceiling_on_a_long_turn() -> None:
    root = _DropTimeoutOnCopyLM(300.0)
    sub = _DropTimeoutOnCopyLM(90.0)
    bound = RLMModelBundle(root, sub).bind_turn_deadline(deadline=time.monotonic() + 840)

    bound.sub_lm.forward(prompt="sub")
    bound.root_lm.forward(prompt="root")

    sub_timeout = bound.sub_lm.calls[-1]["timeout"]
    root_timeout = bound.root_lm.calls[-1]["timeout"]
    assert isinstance(sub_timeout, float)
    assert isinstance(root_timeout, float)
    assert 0 < sub_timeout <= 90
    assert 90 < root_timeout <= 300


def test_remaining_turn_may_only_shrink_the_sub_role_timeout() -> None:
    sub = _DropTimeoutOnCopyLM(90.0)
    bound = RLMModelBundle(_DropTimeoutOnCopyLM(300.0), sub).bind_turn_deadline(deadline=time.monotonic() + 40)

    bound.sub_lm.forward(prompt="sub")

    timeout = bound.sub_lm.calls[-1]["timeout"]
    assert isinstance(timeout, float)
    assert 0 < timeout <= 40


@pytest.mark.asyncio
async def test_sub_role_timeout_ceiling_applies_to_async_forward() -> None:
    sub = _DropTimeoutOnCopyLM(90.0)
    bound = RLMModelBundle(_DropTimeoutOnCopyLM(300.0), sub).bind_turn_deadline(deadline=time.monotonic() + 840)

    await bound.sub_lm.aforward(prompt="sub")

    timeout = bound.sub_lm.calls[-1]["timeout"]
    assert isinstance(timeout, float)
    assert 0 < timeout <= 90


def test_provider_retries_share_the_sub_role_timeout_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr("fleet_rlm.rlm.program.time.monotonic", lambda: clock[0])
    sub = _TimeoutThenRetryLM(90.0, clock, num_retries=1)
    bound = RLMModelBundle(_DropTimeoutOnCopyLM(300.0), sub).bind_turn_deadline(deadline=clock[0] + 840)

    with pytest.raises(TimeoutError, match="Turn LM deadline exceeded"):
        bound.sub_lm.forward(prompt="sub")

    assert len(bound.sub_lm.calls) == 1
    first_timeout = bound.sub_lm.calls[0]["timeout"]
    assert isinstance(first_timeout, float)
    assert 0 < first_timeout <= 90
    assert clock[0] == pytest.approx(1000.0 + first_timeout)


def test_packed_llm_query_then_batched_fits_interpreter_execution_budget() -> None:
    policy = tomllib.loads(Path("config/fleet.toml").read_text(encoding="utf-8"))
    sub_ceiling = float(policy["defaults"]["llm"]["sub"]["timeout_seconds"])
    execution_budget = float(policy["defaults"]["rlm"]["execution_timeout_s"])
    assert policy["defaults"]["llm"]["sub"]["num_retries"] == 1
    assert 2 * sub_ceiling <= execution_budget

    sub = _DropTimeoutOnCopyLM(sub_ceiling)
    bound = RLMModelBundle(
        _DropTimeoutOnCopyLM(float(policy["defaults"]["llm"]["root"]["timeout_seconds"])),
        sub,
    ).bind_turn_deadline(deadline=time.monotonic() + 840)
    bound.sub_lm.forward(prompt="llm_query")
    bound.sub_lm.forward(prompt="llm_query_batched")

    captured = [call["timeout"] for call in bound.sub_lm.calls[-2:]]
    assert all(isinstance(timeout, float) and 0 < timeout <= sub_ceiling for timeout in captured)
    assert sum(captured) <= execution_budget
