"""Opt-in live P45 proof for resident Daytona Session runtime state.

The proof uses a deterministic native RLM action double, so it exercises the
production Daytona interpreter and root-lease provider without sending model
requests.  It proves same-session RLM/interpreter/Sandbox reuse, persistent
Python namespace state, and root rotation for attachment selectors A -> B ->
none.

Run with explicit provider authority::

    FLEET_LIVE=1 uv run pytest tests/live/backend/test_p45_daytona_session_runtime_live.py -q
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from dotenv import load_dotenv

from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.config.settings import Settings
from fleet_rlm.rlm.program import (
    FleetRLMSignature,
    RLMModelBundle,
    RLMOptions,
    build_native_rlm,
)
from fleet_rlm.rlm.runtime import (
    ExecutionRuntime,
    PreparedCapabilities,
    RetainableEnvironmentRelease,
    RLMExecutionContext,
    RLMExecutionSpec,
    RLMRunner,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.session_runtime import SessionKey, SessionRLMRegistry
from fleet_rlm.runtime.bindings import InMemorySandboxBindingStore
from fleet_rlm.runtime.daytona.run_environment import DaytonaRuntimeResources, _DaytonaEnvironmentProvider
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(600)]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_VALUES = frozenset({"1", "true", "yes"})


class _LiveAction:
    """Deterministic RLM action sequence with a persistent namespace witness."""

    def __init__(self) -> None:
        self._responses = iter(
            (
                ("initialize the Session namespace", "p45_marker = 'retained'\nSUBMIT(answer='turn-a1')"),
                ("reuse the Session namespace", "assert p45_marker == 'retained'\nSUBMIT(answer='turn-a2')"),
            )
        )

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        reasoning, code = next(self._responses)
        return dspy.Prediction(reasoning=reasoning, code=code)


class _LiveRLMFactory:
    def __init__(self, action: _LiveAction) -> None:
        self._action = action

    def create(
        self,
        *,
        models: RLMModelBundle,
        options: RLMOptions,
        tools: Sequence[dspy.Tool] | None = None,
        signature: type[dspy.Signature] | str | None = None,
        verbose: bool = True,
    ) -> Any:
        del verbose
        rlm = build_native_rlm(
            signature=signature or FleetRLMSignature,
            options=options,
            tools=tools,
            sub_lm=models.sub_lm,
            verbose=False,
        )
        rlm.generate_action = self._action
        return rlm


@dataclass(slots=True)
class _EmptyCapabilities(PreparedCapabilities):
    spec: RLMExecutionSpec

    @property
    def preparation_notices(self) -> tuple[Any, ...]:
        return ()

    def drain_public_details(self) -> tuple[Any, ...]:
        return ()

    def drain_artifact_candidates(self) -> tuple[Any, ...]:
        return ()

    def drain_memory_candidates(self) -> tuple[Any, ...]:
        return ()

    def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None:
        del attachment_ids

    async def aclose(self) -> None:
        return None


async def _never_cancelled() -> bool:
    return False


def _require_live(settings: Settings) -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        pytest.skip("Set FLEET_LIVE=1 for the P45 Daytona Session-runtime proof")
    if settings.daytona_api_key is None:
        pytest.skip("FLEET_DAYTONA_API_KEY not configured")
    if not settings.daytona_snapshot:
        pytest.skip("FLEET_DAYTONA_SNAPSHOT not configured")


def _claim(
    *,
    user_id: UUID,
    workspace_id: UUID,
    session_id: UUID,
    attachment_ids: tuple[UUID, ...] = (),
) -> ClaimedRun:
    return ClaimedRun(
        run_id=uuid4(),
        session_id=session_id,
        access=TurnAccess(user_id, workspace_id),
        input=TurnInput("P45 live Session runtime proof", attachment_ids),
        history=SessionHistory(),
        cancellation_requested=_never_cancelled,
        _claim=_RunClaimToken(uuid4()),
    )


def _sandbox_id(environment: Any) -> str:
    sandbox = getattr(environment.attachment_sink, "_sandbox", None)
    value = getattr(sandbox, "id", None)
    assert isinstance(value, str) and value
    return value


def _context(
    run: ClaimedRun,
    *,
    interpreter: Any,
    models: RLMModelBundle,
    environment_release: RetainableEnvironmentRelease | None,
    history: dspy.History | CommittedSessionHistory,
) -> RLMExecutionContext:
    return RLMExecutionContext(
        identity=RunIdentity(run.run_id, run.session_id, run.access, run.authority),
        session=SessionView(
            request=run.input.text,
            session_context=SessionContextManifest(run.session_id, 0, 0, ()),
            attachments=(),
            history=history,
        ),
        execution=ExecutionRuntime(
            models=models,
            options=RLMOptions(max_iters=1, max_llm_calls=1, max_output_chars=1024),
            interpreter=interpreter,
            cancellation_requested=_never_cancelled,
            deadline=time.monotonic() + 180,
            environment_release=environment_release,
        ),
        capabilities=_EmptyCapabilities(RLMExecutionSpec(signature=FleetRLMSignature)),
    )


async def _run_committed(
    runner: RLMRunner,
    context: RLMExecutionContext,
    *,
    preparation_release: Any,
) -> None:
    stream = runner.stream(context)
    try:
        _ = [event async for event in stream]
        assert stream.outcome is not None and stream.outcome.succeeded
        stream.mark_committed()
    finally:
        await stream.aclose()
        # Direct provider acquisition bypasses DefaultRunPreparer, so this
        # helper must release the per-Turn preparation reservation explicitly.
        await preparation_release()


def _resources(settings: Settings, cleanup: RunCleanupSupervisor) -> DaytonaRuntimeResources:
    return DaytonaRuntimeResources(
        settings,
        bindings=InMemorySandboxBindingStore(),
        cleanup=cleanup,
        max_active_leases=settings.max_active_daytona_leases,
        execution_output_cap=settings.rlm_max_execution_output_chars,
        execution_timeout_s=settings.rlm_execution_timeout_s,
    )


@pytest.mark.asyncio
async def test_live_daytona_reuses_session_runtime_and_rotates_attachment_roots() -> None:
    load_dotenv(_REPO_ROOT / ".env", override=False)
    settings = load_runtime_settings()
    _require_live(settings)

    user_id, workspace_id, session_id = uuid4(), uuid4(), uuid4()
    attachment_a, attachment_b = uuid4(), uuid4()
    cleanup = RunCleanupSupervisor(max_jobs=8)
    resources = _resources(settings, cleanup)
    registry = SessionRLMRegistry()
    provider = _DaytonaEnvironmentProvider(resources, settings, registry)
    runner = RLMRunner(factory=_LiveRLMFactory(_LiveAction()), runtime_registry=registry)
    models = RLMModelBundle(
        dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
    )
    environment_a = None

    try:
        # With no bound Attachment manifest, compatible Turns reuse the same
        # root Sandbox and caller-owned interpreter.
        run_1 = _claim(user_id=user_id, workspace_id=workspace_id, session_id=session_id)
        environment_a = await provider.acquire(run_1, deadline=time.monotonic() + 180)
        sandbox_a = _sandbox_id(environment_a)
        assert environment_a.interpreter is not None
        resident_release = RetainableEnvironmentRelease(environment_a.resident_release or environment_a.release)
        resident_release.retain()
        await _run_committed(
            runner,
            _context(
                run_1,
                interpreter=environment_a.interpreter,
                models=models,
                environment_release=resident_release,
                history=environment_a.history_transport,
            ),
            preparation_release=environment_a.release,
        )

        run_2 = _claim(user_id=user_id, workspace_id=workspace_id, session_id=session_id)
        environment_a2 = await provider.acquire(run_2, deadline=time.monotonic() + 180)
        assert _sandbox_id(environment_a2) == sandbox_a
        assert environment_a2.interpreter is environment_a.interpreter
        await _run_committed(
            runner,
            _context(
                run_2,
                interpreter=environment_a2.interpreter,
                models=models,
                environment_release=None,
                history=environment_a2.history_transport,
            ),
            preparation_release=environment_a2.release,
        )

        key = SessionKey(str(workspace_id), str(session_id))
        state_a = registry.get(key)
        assert state_a is not None
        assert state_a.interpreter is environment_a.interpreter
        assert state_a.rlm is not None

        # Attachment staging is Run-scoped.  Even the same durable Attachment
        # ID gets a new manifest path, so every transition rotates the root.
        run_attachment_a = _claim(
            user_id=user_id, workspace_id=workspace_id, session_id=session_id, attachment_ids=(attachment_a,)
        )
        environment_attachment_a = await provider.acquire(run_attachment_a, deadline=time.monotonic() + 180)
        sandbox_attachment_a = _sandbox_id(environment_attachment_a)
        assert sandbox_attachment_a != sandbox_a
        assert environment_attachment_a.interpreter is not environment_a.interpreter
        await environment_attachment_a.release()

        run_b = _claim(
            user_id=user_id, workspace_id=workspace_id, session_id=session_id, attachment_ids=(attachment_b,)
        )
        environment_b = await provider.acquire(run_b, deadline=time.monotonic() + 180)
        assert _sandbox_id(environment_b) != sandbox_attachment_a
        assert environment_b.interpreter is not environment_attachment_a.interpreter
        await environment_b.release()

        run_none = _claim(user_id=user_id, workspace_id=workspace_id, session_id=session_id)
        environment_none = await provider.acquire(run_none, deadline=time.monotonic() + 180)
        assert _sandbox_id(environment_none) != _sandbox_id(environment_b)
        await environment_none.release()

    finally:
        await registry.shutdown()
        await provider.aclose()
        await cleanup.shutdown(drain_seconds=30)
        # Daytona deletion is asynchronous. Retry the bounded resource owner
        # until every tracked identity reaches the confirmed-absent boundary.
        for _ in range(120):
            if await resources.adispose(drain_seconds=30):
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail("Daytona resource cleanup did not settle")
