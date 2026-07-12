"""impl-02: RLMRunner streams RuntimeEvents with fakes."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.events import RuntimeEventKind
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.rlm.runner import RLMRunner


class _FakeLease:
    def __init__(self, interpreter: Any = None) -> None:
        self.interpreter = interpreter if interpreter is not None else MagicMock(name="interp")
        self.released = 0

    def release(self) -> None:
        self.released += 1


class _FakeRLM:
    """Stand-in module: exposes sub_lm and async aforward."""

    def __init__(
        self,
        *,
        answer: str = "done",
        sub_lm: Any = None,
        fail: BaseException | None = None,
        capture: dict[str, Any] | None = None,
    ) -> None:
        self.answer = answer
        self.sub_lm = sub_lm
        self.fail = fail
        self.capture = capture if capture is not None else {}

    async def aforward(self, **kwargs: Any) -> Any:
        import dspy

        self.capture["request"] = kwargs.get("request")
        self.capture["settings_lm"] = getattr(dspy.settings, "lm", None)
        if self.fail is not None:
            raise self.fail
        prediction = dspy.Prediction(answer=self.answer)
        return prediction


class _FakeFactory:
    def __init__(self, rlm: Any | None = None, *, maker: Any = None) -> None:
        self._rlm = rlm
        self._maker = maker
        self.created: list[Any] = []
        self.last_kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        if self._maker is not None:
            instance = self._maker(**kwargs)
        else:
            instance = self._rlm
        self.created.append(instance)
        return instance


def _context(
    *,
    lease: _FakeLease | None = None,
    root: Any | None = None,
    sub: Any | None = None,
    request: str = "hello",
) -> tuple[RLMTurnContext, _FakeLease]:
    lease = lease or _FakeLease()
    root = root if root is not None else MagicMock(name="root_lm")
    sub = sub if sub is not None else MagicMock(name="sub_lm")
    ctx = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request=request,
        models=RLMModelBundle(root_lm=root, sub_lm=sub),
        budget=RLMBudget(max_iterations=3, max_llm_calls=5, max_output_chars=1000),
        lease=lease,
    )
    return ctx, lease


async def _collect(runner: RLMRunner, context: RLMTurnContext) -> tuple[list[Any], Any]:
    stream = runner.stream(context)
    events = [event async for event in stream]
    return events, stream.outcome


@pytest.mark.asyncio
async def test_runner_emits_start_text_usage_and_one_terminal() -> None:
    sub = MagicMock(name="sub_lm")
    fake_rlm = _FakeRLM(answer="world", sub_lm=sub)
    factory = _FakeFactory(fake_rlm)
    ctx, lease = _context(sub=sub)

    events, outcome = await _collect(RLMRunner(factory=factory), ctx)
    kinds = [e.kind for e in events]

    assert kinds[0] == RuntimeEventKind.RUN_STARTED
    assert RuntimeEventKind.STATUS in kinds
    assert RuntimeEventKind.TEXT_DELTA in kinds
    assert RuntimeEventKind.TEXT_COMPLETED in kinds
    assert RuntimeEventKind.USAGE in kinds
    assert RuntimeEventKind.RUN_COMPLETED not in kinds
    assert RuntimeEventKind.ERROR not in kinds
    assert outcome is not None
    assert outcome.terminal_status == "completed"
    assert outcome.assistant_text == "world"
    assert lease.released == 1
    assert factory.last_kwargs["models"].sub_lm is sub
    assert factory.last_kwargs["interpreter"] is lease.interpreter


@pytest.mark.asyncio
async def test_runner_applies_root_lm_via_dspy_settings_context() -> None:
    root = MagicMock(name="root_lm")
    capture: dict[str, Any] = {}
    fake_rlm = _FakeRLM(answer="ok", capture=capture)
    ctx, _lease = _context(root=root)

    await _collect(RLMRunner(factory=_FakeFactory(fake_rlm)), ctx)

    assert capture["settings_lm"] is root
    assert capture["request"] == "hello"


@pytest.mark.asyncio
async def test_runner_sanitizes_failures_and_still_releases_lease() -> None:
    secret = "api_key=sk-super-secret-value"
    fake_rlm = _FakeRLM(fail=RuntimeError(f"provider boom {secret} /Users/zoe/secret/path"))
    ctx, lease = _context()

    events, outcome = await _collect(RLMRunner(factory=_FakeFactory(fake_rlm)), ctx)
    kinds = [e.kind for e in events]

    assert RuntimeEventKind.ERROR not in kinds
    assert outcome is not None
    assert outcome.terminal_status == "failed"
    message = outcome.public_error_message or ""
    assert "sk-super-secret-value" not in message
    assert "/Users/zoe" not in message
    assert lease.released == 1


@pytest.mark.asyncio
async def test_concurrent_runs_use_distinct_rlm_instances() -> None:
    instances: list[Any] = []

    def maker(**_kwargs: Any) -> _FakeRLM:
        rlm = _FakeRLM(answer=f"n{len(instances)}")
        instances.append(rlm)
        return rlm

    factory = _FakeFactory(maker=maker)
    runner = RLMRunner(factory=factory)
    ctx_a, lease_a = _context(request="a")
    ctx_b, lease_b = _context(request="b")

    results = await asyncio.gather(_collect(runner, ctx_a), _collect(runner, ctx_b))

    assert len(factory.created) == 2
    assert factory.created[0] is not factory.created[1]
    assert all(outcome is not None and outcome.terminal_status == "completed" for _events, outcome in results)
    assert lease_a.released == 1
    assert lease_b.released == 1


@pytest.mark.asyncio
async def test_sequences_strictly_increase_for_one_run() -> None:
    ctx, _ = _context()
    events, _outcome = await _collect(RLMRunner(factory=_FakeFactory(_FakeRLM())), ctx)
    sequences = [e.sequence for e in events]
    assert sequences == list(range(1, len(sequences) + 1))
