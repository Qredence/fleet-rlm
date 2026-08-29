"""Opt-in P53 live certification for Session runtime rotation semantics.

This lane deliberately uses a deterministic native-RLM action sequence. The
provider, interpreter, Session registry, SQL-backed Turn lifecycle, durable
History, and Workspace Volume are production implementations; only the model
action stream and fault toggles are deterministic so the certification is
repeatable at the LLM boundary. The composition is assembled with the same
DefaultRunPreparer/TurnRuntime path while keeping the action double explicit.

Run through the serial evidence runner (it supplies the invocation nonce)::

    FLEET_LIVE=1 DAYTONA_TARGET=us \
        uv run python scripts/live_p53_certification.py

For direct debugging only, also set ``FLEET_P53_RUN_ID`` and an evidence path;
the resulting receipt is not certification until the runner aggregates it.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from dotenv import load_dotenv

from fleet_rlm.attachments.models import PreparedAttachments
from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.preparation import (
    DefaultRunPreparer,
    RunEnvironment,
)
from fleet_rlm.chat.run_claim import HeartbeatClaim
from fleet_rlm.chat.run_lifecycle import (
    ClaimedRun,
    RunLifecycleService,
    RunLifecycleUnavailableError,
)
from fleet_rlm.chat.turn_runtime import TurnRuntime
from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.config.settings import Settings
from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory
from fleet_rlm.persistence.repositories import (
    SqlAlchemyRunStateStore,
    SqlAlchemySandboxBindingStore,
    SqlAlchemySessionCatalog,
)
from fleet_rlm.rlm.program import FleetRLMSignature, RLMModelBundle, RLMOptions, build_native_rlm
from fleet_rlm.rlm.runtime import PreparedCapabilities, RLMExecutionSpec, RLMRunner
from fleet_rlm.rlm.session_runtime import SessionKey, SessionRLMRegistry
from fleet_rlm.runtime.cleanup import RunCleanupSupervisor
from fleet_rlm.runtime.daytona.run_environment import DaytonaRuntimeResources, _DaytonaEnvironmentProvider
from fleet_rlm.sessions.models import TurnAccess, TurnInput
from tests.live.backend._database import upgrade_to_head

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1800)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_CERTIFIED_DSPY = "3.3.1"
_CERTIFIED_DAYTONA_SNAPSHOT = "fleet-rlm-python313-v5"
_CERTIFIED_DAYTONA_TARGET = "us"
_SCHEMA = "fleet.p53-live-session-certification/v1"
_RUN_ID_ENV = "FLEET_P53_RUN_ID"


class _P53Action:
    """Finite, typed action stream for the live native RLMs."""

    def __init__(self) -> None:
        self._responses = [
            ("turn-1", "p53_retained_marker = 'retained'\nSUBMIT(answer='turn-1')"),
            (
                "turn-2",
                "assert p53_retained_marker == 'retained'\n"
                "assert len(history.messages) == 1\n"
                "assert history.messages[0]['request'] == 'P53 turn 1'\n"
                "assert history.messages[0]['answer'] == 'turn-1'\n"
                "SUBMIT(answer='turn-2')",
            ),
            ("commit-failure", "p53_commit_marker = True\nSUBMIT(answer='not durable')"),
            (
                "after-commit-failure",
                "assert 'p53_commit_marker' not in globals()\n"
                "assert 'p53_retained_marker' not in globals()\n"
                "assert len(history.messages) == 2\n"
                "assert history.messages[1]['answer'] == 'turn-2'\n"
                "SUBMIT(answer='after-commit-failure')",
            ),
            (
                "timeout",
                "p53_timeout_marker = True\nimport time\ntime.sleep(1.0)\nSUBMIT(answer='timeout-not-durable')",
            ),
            (
                "after-timeout",
                "assert 'p53_timeout_marker' not in globals()\n"
                "assert len(history.messages) == 3\n"
                "SUBMIT(answer='after-timeout')",
            ),
            (
                "cancellation",
                "p53_cancel_marker = True\nimport time\ntime.sleep(1.0)\nSUBMIT(answer='cancel-not-durable')",
            ),
            (
                "after-cancellation",
                "assert 'p53_cancel_marker' not in globals()\n"
                "assert len(history.messages) == 4\n"
                "SUBMIT(answer='after-cancellation')",
            ),
            (
                "after-provider-failure",
                "assert len(history.messages) == 5\n"
                "assert 'p53_retained_marker' not in globals()\n"
                "SUBMIT(answer='after-provider-failure')",
            ),
            (
                "claim-loss",
                "p53_claim_marker = True\nimport time\ntime.sleep(1.0)\nSUBMIT(answer='claim-loss-not-durable')",
            ),
            (
                "after-claim-loss",
                "assert 'p53_claim_marker' not in globals()\n"
                "assert len(history.messages) == 6\n"
                "SUBMIT(answer='after-claim-loss')",
            ),
            (
                "after-fingerprint",
                "assert 'p53_claim_marker' not in globals()\n"
                "assert len(history.messages) == 7\n"
                "assert all(name not in globals() for name in ("
                "'p53_commit_marker', 'p53_timeout_marker', 'p53_cancel_marker', 'p53_claim_marker'))\n"
                "SUBMIT(answer='after-fingerprint')",
            ),
            (
                "after-idle-eviction",
                "assert 'p53_retained_marker' not in globals()\n"
                "assert all(name not in globals() for name in ("
                "'p53_commit_marker', 'p53_timeout_marker', 'p53_cancel_marker', 'p53_claim_marker'))\n"
                "assert len(history.messages) == 8\n"
                "SUBMIT(answer='after-idle-eviction')",
            ),
        ]
        self._index = 0
        self.calls: list[str] = []

    async def acall(self, **kwargs: Any) -> dspy.Prediction:
        if self._index >= len(self._responses):
            raise AssertionError("P53 action stream was called more often than specified")
        label, code = self._responses[self._index]
        self._index += 1
        request = kwargs.get("request")
        self.calls.append(f"{label}:{request}" if isinstance(request, str) else label)
        return dspy.Prediction(reasoning=label, code=code)


class _P53FaultingRunner(RLMRunner):
    """Apply timeout/cancel at the host worker boundary, not in model code."""

    def stream(self, context: Any) -> Any:
        request = context.session.request
        if request == "P53 timeout":
            context = replace(
                context,
                execution=replace(context.execution, deadline=asyncio.get_running_loop().time() - 1),
            )
        elif request == "P53 cancellation":
            calls = 0

            async def cancellation_requested() -> bool:
                nonlocal calls
                calls += 1
                return calls > 1

            context = replace(
                context,
                execution=replace(context.execution, cancellation_requested=cancellation_requested),
            )
        return super().stream(context)


class _P53PlatformGetFault:
    """Inject one post-acquisition provider lookup outcome without changing the manager."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.mode: str | None = None

    async def get(self, sandbox_id: str) -> Any | None:
        mode, self.mode = self.mode, None
        if mode == "none":
            return None
        if mode == "raises":
            raise RuntimeError("injected P53 provider lookup failure")
        return await self._delegate.get(sandbox_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class _P53RLMFactory:
    def __init__(self, action: _P53Action) -> None:
        self.action = action
        self.created: list[Any] = []

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
        rlm.generate_action = self.action
        self.created.append(rlm)
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


@dataclass(slots=True)
class _EmptyAttachmentPreparer:
    async def prepare_run(self, *_args: Any, **_kwargs: Any) -> PreparedAttachments:
        return PreparedAttachments((), ())


@dataclass(slots=True)
class _EmptyCapabilityPreparer:
    async def prepare(
        self,
        run: ClaimedRun,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> PreparedCapabilities:
        del run, environment, attachments, deadline
        return _EmptyCapabilities(RLMExecutionSpec(signature=FleetRLMSignature))


class _ScenarioStore(SqlAlchemyRunStateStore):
    """Production SQL lifecycle store with controlled commit/heartbeat faults."""

    def __init__(self, session_factory: Any) -> None:
        super().__init__(session_factory, stale_after_seconds=600)
        self.fail_commit_once = False
        self.fail_heartbeats = False

    async def commit(
        self,
        run: ClaimedRun,
        committed: Any,
        artifacts: tuple[Any, ...],
        memory_intents: tuple[Any, ...] = (),
    ) -> Any:
        if self.fail_commit_once:
            self.fail_commit_once = False
            raise RuntimeError("injected P53 commit failure")
        return await super().commit(run, committed, artifacts, memory_intents=memory_intents)

    async def transition_claim(self, run: ClaimedRun, command: Any) -> Any:
        if self.fail_heartbeats and isinstance(command, HeartbeatClaim):
            raise RunLifecycleUnavailableError("injected P53 claim loss")
        return await super().transition_claim(run, command)


def _identity() -> dict[str, Any]:
    sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ("git", "status", "--porcelain", "--untracked-files=all"),
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    unexpected = [line for line in status if line and not line.startswith("?? .factory/")]
    lockfile_sha256 = hashlib.sha256((_REPO_ROOT / "uv.lock").read_bytes()).hexdigest()
    return {
        "sha": sha,
        "lockfile_sha256": lockfile_sha256,
        "dspy": importlib.metadata.version("dspy"),
        "daytona_snapshot": os.environ.get("FLEET_DAYTONA_SNAPSHOT"),
        "daytona_target": os.environ.get("DAYTONA_TARGET"),
        "tracked_tree_clean": not unexpected,
    }


def _write_receipt(payload: dict[str, Any]) -> None:
    configured = os.environ.get(_EVIDENCE_ENV)
    if not configured:
        return
    path = Path(configured).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _require_live(settings: Settings) -> None:
    load_dotenv(_REPO_ROOT / ".env", override=False)
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        pytest.skip("Set FLEET_LIVE=1 for the P53 Daytona Session certification")
    if settings.daytona_api_key is None:
        pytest.skip("FLEET_DAYTONA_API_KEY not configured")
    if settings.daytona_snapshot != _CERTIFIED_DAYTONA_SNAPSHOT:
        pytest.fail("P53 certification requires the authoritative Daytona v5 snapshot")
    if os.environ.get("DAYTONA_TARGET") != _CERTIFIED_DAYTONA_TARGET:
        pytest.fail("P53 certification requires unquoted DAYTONA_TARGET=us")
    if not os.environ.get(_RUN_ID_ENV):
        pytest.fail("P53 certification requires a runner invocation id")


async def _wait_cleanup(cleanup: RunCleanupSupervisor) -> None:
    for _ in range(100):
        if cleanup.active_jobs == 0:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("P53 detached cleanup did not settle")


@dataclass(frozen=True, slots=True)
class _TurnResult:
    events: tuple[Any, ...]
    error_type: str | None
    terminal_type: str | None
    terminal_code: str | None


async def _run_turn(
    coordinator: TurnRuntime,
    access: TurnAccess,
    session_id: UUID,
    *,
    text: str,
    ordinal: int,
) -> _TurnResult:
    opened = None
    events: list[Any] = []
    error_type: str | None = None
    try:
        opened = await coordinator.open(
            OpenTurnCommand(
                access=access,
                session_id=session_id,
                input=TurnInput(text),
                idempotency_key=f"p53-{ordinal}-{uuid4()}",
                proposed_run_id=uuid4(),
            )
        )
        try:
            async for event in opened:
                events.append(event)
        except BaseException as exc:
            error_type = type(exc).__name__
    except BaseException as exc:
        error_type = None if isinstance(exc, asyncio.CancelledError) else type(exc).__name__
    finally:
        if opened is not None:
            try:
                await opened.aclose()
            except BaseException as exc:
                if error_type is None:
                    error_type = type(exc).__name__
    terminal = events[-1].detail if events else None
    return _TurnResult(
        tuple(events),
        error_type,
        type(terminal).__name__ if terminal is not None else None,
        str(getattr(terminal, "code", "")) or None,
    )


def _sandbox_id(binding: Any) -> str | None:
    value = getattr(binding, "sandbox_id", None)
    return str(value) if value else None


def _scenario_record(
    *,
    name: str,
    before: Any,
    after: Any,
    result: _TurnResult,
    history_count: int,
    failed_markers_absent: bool,
    provider_state: str | None,
    provider_before: Any | None = None,
    provider_after: Any | None = None,
    provider_before_state: str | None = None,
) -> dict[str, Any]:
    before_sandbox = provider_before
    if before_sandbox is None:
        before_sandbox = before.get("sandbox_id") if isinstance(before, dict) else None
    after_sandbox = provider_after
    if after_sandbox is None:
        after_sandbox = after.get("sandbox_id") if isinstance(after, dict) else None
    return {
        "passed": True,
        "trigger": name,
        "terminal": {
            "type": result.terminal_type,
            "code": result.terminal_code,
            "error_type": result.error_type,
        },
        "old_runtime": {
            "generation": getattr(before, "generation", None),
            "closed": bool(getattr(before, "closed", False)),
            "rlm_id": str(id(getattr(before, "rlm", None))),
            "interpreter_id": str(id(getattr(before, "interpreter", None))),
        },
        "new_runtime": {
            "generation": getattr(after, "generation", None),
            "rlm_id": str(id(getattr(after, "rlm", None))),
            "interpreter_id": str(id(getattr(after, "interpreter", None))),
        },
        "provider": {
            "before_sandbox_id": before_sandbox,
            "after_sandbox_id": after_sandbox,
            "before_state": provider_before_state or "running",
            "after_state": provider_state,
        },
        "continuation": {
            "history_before_count": history_count - 1,
            "history_message_count": history_count,
            "history_after_count": history_count,
            "failed_python_markers_absent": failed_markers_absent,
            "admission_restored": True,
        },
        "handoff": {
            "interpreter_preserved": before is not None
            and after is not None
            and getattr(before, "interpreter", None) is getattr(after, "interpreter", None),
        },
    }


@pytest.mark.asyncio
async def test_live_p53_daytona_session_rotations_and_history(tmp_path: Path) -> None:
    """Prove resident reuse and every P53.2 runtime rotation trigger."""
    load_dotenv(_REPO_ROOT / ".env", override=False)
    settings = load_runtime_settings().model_copy(
        update={
            "volume_name": f"fleet-rlm-p53-{uuid4()}",
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'p53.db').resolve()}",
            "rlm_max_iters": 1,
            "rlm_max_llm_calls": 1,
            "rlm_max_output_chars": 2048,
            "rlm_max_execution_output_chars": 2048,
            "rlm_execution_timeout_s": 30,
        }
    )
    _require_live(settings)
    identity = _identity()
    assert identity["dspy"] == _CERTIFIED_DSPY
    assert identity["tracked_tree_clean"] is True, "P53 evidence requires a clean candidate"

    access = TurnAccess(uuid4(), uuid4())
    assert settings.database_url is not None
    upgrade_to_head(settings.database_url)
    engine = create_async_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)
    session_catalog = SqlAlchemySessionCatalog(session_factory)
    session_record = await session_catalog.create(
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title="P53 Daytona Session certification",
    )
    session_id = session_record.id
    cleanup = RunCleanupSupervisor(max_jobs=16)
    resources = DaytonaRuntimeResources(
        settings,
        bindings=SqlAlchemySandboxBindingStore(session_factory),
        cleanup=cleanup,
        max_active_leases=settings.max_active_daytona_leases,
        idle_stop_seconds=0.1,
        execution_output_cap=settings.rlm_max_execution_output_chars,
        execution_timeout_s=settings.rlm_execution_timeout_s,
    )
    platform_get_fault = _P53PlatformGetFault(resources.platform)
    resources.platform = platform_get_fault
    registry = SessionRLMRegistry(idle_timeout=1.0)
    provider = _DaytonaEnvironmentProvider(resources, settings, registry)
    action = _P53Action()
    factory = _P53RLMFactory(action)
    models = RLMModelBundle(
        dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
    )
    options = RLMOptions(max_iters=1, max_llm_calls=1, max_output_chars=2048)
    preparation = DefaultRunPreparer(
        models=models,
        options=options,
        attachments=_EmptyAttachmentPreparer(),
        environments=provider,
        capabilities=_EmptyCapabilityPreparer(),
        session_runtime_registry=registry,
    )
    store = _ScenarioStore(session_factory)
    lifecycle = RunLifecycleService(
        store,
        max_artifact_bytes=settings.max_artifact_bytes,
        heartbeat_seconds=0.05,
        stale_after_seconds=0.15,
        cleanup=cleanup,
    )
    coordinator = TurnRuntime(
        lifecycle=lifecycle,
        preparation=preparation,
        runner=_P53FaultingRunner(factory=factory, runtime_registry=registry),
        cleanup=cleanup,
        turn_timeout_seconds=180,
        claim_loss_fence=resources.session_manager.fence_session,
    )
    key = SessionKey(str(access.workspace_id), str(session_id))
    scenarios: dict[str, dict[str, Any]] = {}
    sandbox_ids: set[str] = set()

    async def successful_turn(text: str, ordinal: int) -> _TurnResult:
        result = await _run_turn(coordinator, access, session_id, text=text, ordinal=ordinal)
        await _wait_cleanup(cleanup)
        return result

    try:
        first = await successful_turn("P53 turn 1", 1)
        assert first.terminal_type == "RunCompleted"
        state_1 = registry.get(key)
        assert state_1 is not None
        first_binding = await resources.bindings.get(session_id)
        first_sandbox = _sandbox_id(first_binding)
        assert first_sandbox is not None
        sandbox_ids.add(first_sandbox)

        second = await successful_turn("P53 turn 2", 2)
        assert second.terminal_type == "RunCompleted"
        state_2 = registry.get(key)
        assert state_2 is state_1
        assert state_2.rlm is factory.created[0]
        second_binding = await resources.bindings.get(session_id)
        assert _sandbox_id(second_binding) == first_sandbox
        assert len(factory.created) == 1

        # The successful pair proves A1-A4: same resident objects, Python
        # namespace continuity, and the claimed committed History checkpoint.
        scenarios["resident_continuity"] = {
            "passed": True,
            "turns": 2,
            "same_rlm": True,
            "same_interpreter": True,
            "same_sandbox": True,
            "python_variable_continuity": True,
            "complete_history_continuity": True,
            "history_message_count": 1,
            "history_after_count": 2,
        }

        store.fail_commit_once = True
        before_commit = registry.get(key)
        assert before_commit is not None
        before_commit_binding = await resources.bindings.get(session_id)
        before_commit_sandbox = _sandbox_id(before_commit_binding)
        assert before_commit_sandbox is not None
        commit_failure = await successful_turn("P53 commit failure", 3)
        assert commit_failure.terminal_type == "RunFailed"
        assert commit_failure.terminal_code == "commit_failed"
        # The next preparation closes the tainted resident before admission.
        after_commit_turn = await successful_turn("P53 after commit failure", 4)
        assert after_commit_turn.terminal_type == "RunCompleted"
        new_commit = registry.get(key)
        assert new_commit is not None and new_commit is not before_commit
        assert before_commit.closed
        after_commit_binding = await resources.bindings.get(session_id)
        after_commit_sandbox = _sandbox_id(after_commit_binding)
        assert after_commit_sandbox is not None
        scenarios["commit_failure"] = _scenario_record(
            name="commit_failure",
            before=before_commit,
            after=new_commit,
            result=commit_failure,
            history_count=3,
            failed_markers_absent=True,
            provider_state=getattr(after_commit_binding, "provider_state", None),
            provider_before=before_commit_sandbox,
            provider_after=after_commit_sandbox,
            provider_before_state=getattr(before_commit_binding, "provider_state", None),
        )

        before_timeout = registry.get(key)
        assert before_timeout is not None
        before_timeout_binding = await resources.bindings.get(session_id)
        before_timeout_sandbox = _sandbox_id(before_timeout_binding)
        assert before_timeout_sandbox is not None
        timeout = await successful_turn("P53 timeout", 5)
        assert timeout.terminal_type == "RunTimedOut"
        after_timeout = await successful_turn("P53 after timeout", 6)
        assert after_timeout.terminal_type == "RunCompleted"
        new_timeout = registry.get(key)
        assert new_timeout is not None and new_timeout is not before_timeout
        assert before_timeout.closed
        after_timeout_binding = await resources.bindings.get(session_id)
        after_timeout_sandbox = _sandbox_id(after_timeout_binding)
        assert after_timeout_sandbox is not None
        scenarios["timeout"] = _scenario_record(
            name="timeout",
            before=before_timeout,
            after=new_timeout,
            result=timeout,
            history_count=4,
            failed_markers_absent=True,
            provider_state=getattr(after_timeout_binding, "provider_state", None),
            provider_before=before_timeout_sandbox,
            provider_after=after_timeout_sandbox,
            provider_before_state=getattr(before_timeout_binding, "provider_state", None),
        )

        before_cancel = registry.get(key)
        assert before_cancel is not None
        before_cancel_binding = await resources.bindings.get(session_id)
        before_cancel_sandbox = _sandbox_id(before_cancel_binding)
        assert before_cancel_sandbox is not None
        cancellation = await successful_turn("P53 cancellation", 7)
        assert cancellation.terminal_type == "RunCancelled"
        after_cancel = await successful_turn("P53 after cancellation", 8)
        assert after_cancel.terminal_type == "RunCompleted"
        new_cancel = registry.get(key)
        assert new_cancel is not None and new_cancel is not before_cancel
        assert before_cancel.closed
        after_cancel_binding = await resources.bindings.get(session_id)
        after_cancel_sandbox = _sandbox_id(after_cancel_binding)
        assert after_cancel_sandbox is not None
        scenarios["cancellation"] = _scenario_record(
            name="cancellation",
            before=before_cancel,
            after=new_cancel,
            result=cancellation,
            history_count=5,
            failed_markers_absent=True,
            provider_state=getattr(after_cancel_binding, "provider_state", None),
            provider_before=before_cancel_sandbox,
            provider_after=after_cancel_sandbox,
            provider_before_state=getattr(before_cancel_binding, "provider_state", None),
        )

        binding_before_provider = await resources.bindings.get(session_id)
        old_sandbox = _sandbox_id(binding_before_provider)
        assert old_sandbox is not None
        sandbox_ids.add(old_sandbox)
        # Fault the platform lookup performed by DaytonaRuntime after the
        # manager has acquired and persisted a fresh lease, before the public
        # RootSessionLease can be published.  The manager remains the real
        # provider owner so quarantine/release is exercised end to end.
        platform_get_fault.mode = "raises"
        provider_failure = await _run_turn(coordinator, access, session_id, text="P53 provider failure", ordinal=9)
        await _wait_cleanup(cleanup)
        assert provider_failure.error_type is not None
        provider_state_before = registry.get(key)
        assert provider_state_before is not None
        after_provider = await successful_turn("P53 after provider failure", 10)
        assert after_provider.terminal_type == "RunCompleted"
        new_provider = registry.get(key)
        assert new_provider is not None
        new_binding = await resources.bindings.get(session_id)
        new_sandbox = _sandbox_id(new_binding)
        assert new_sandbox is not None
        sandbox_ids.add(new_sandbox)
        assert new_sandbox != old_sandbox
        assert provider_state_before.closed
        scenarios["provider_failure"] = _scenario_record(
            name="provider_failure",
            before=provider_state_before,
            after=new_provider,
            result=provider_failure,
            history_count=6,
            failed_markers_absent=True,
            provider_state=getattr(new_binding, "provider_state", None),
            provider_before=old_sandbox,
            provider_after=new_sandbox,
            provider_before_state=getattr(binding_before_provider, "provider_state", None),
        )

        store.fail_heartbeats = True
        before_claim = registry.get(key)
        assert before_claim is not None
        before_claim_binding = await resources.bindings.get(session_id)
        before_claim_sandbox = _sandbox_id(before_claim_binding)
        assert before_claim_sandbox is not None
        claim_loss = await successful_turn("P53 claim loss", 11)
        store.fail_heartbeats = False
        assert claim_loss.terminal_type == "RunFailed"
        after_claim = await successful_turn("P53 after claim loss", 12)
        assert after_claim.terminal_type == "RunCompleted"
        new_claim = registry.get(key)
        assert new_claim is not None and new_claim is not before_claim
        assert before_claim.closed
        after_claim_binding = await resources.bindings.get(session_id)
        after_claim_sandbox = _sandbox_id(after_claim_binding)
        assert after_claim_sandbox is not None
        scenarios["claim_loss"] = _scenario_record(
            name="claim_loss",
            before=before_claim,
            after=new_claim,
            result=claim_loss,
            history_count=7,
            failed_markers_absent=True,
            provider_state=getattr(after_claim_binding, "provider_state", None),
            provider_before=before_claim_sandbox,
            provider_after=after_claim_sandbox,
            provider_before_state=getattr(before_claim_binding, "provider_state", None),
        )

        before_fingerprint = registry.get(key)
        assert before_fingerprint is not None
        before_fingerprint_binding = await resources.bindings.get(session_id)
        before_fingerprint_sandbox = _sandbox_id(before_fingerprint_binding)
        assert before_fingerprint_sandbox is not None
        preparation._options = RLMOptions(max_iters=2, max_llm_calls=1, max_output_chars=2048)
        fingerprint = await successful_turn("P53 fingerprint change", 13)
        assert fingerprint.terminal_type == "RunCompleted"
        new_fingerprint = registry.get(key)
        assert new_fingerprint is not None and new_fingerprint is not before_fingerprint
        assert before_fingerprint.closed
        assert new_fingerprint.rlm is not before_fingerprint.rlm
        assert new_fingerprint.interpreter is before_fingerprint.interpreter
        after_fingerprint_binding = await resources.bindings.get(session_id)
        after_fingerprint_sandbox = _sandbox_id(after_fingerprint_binding)
        assert after_fingerprint_sandbox is not None
        scenarios["fingerprint_change"] = _scenario_record(
            name="fingerprint_change",
            before=before_fingerprint,
            after=new_fingerprint,
            result=fingerprint,
            history_count=8,
            failed_markers_absent=True,
            provider_state=getattr(after_fingerprint_binding, "provider_state", None),
            provider_before=before_fingerprint_sandbox,
            provider_after=after_fingerprint_sandbox,
            provider_before_state=getattr(before_fingerprint_binding, "provider_state", None),
        )

        before_idle = registry.get(key)
        assert before_idle is not None
        before_idle_binding = await resources.bindings.get(session_id)
        before_idle_sandbox = _sandbox_id(before_idle_binding)
        assert before_idle_sandbox is not None
        evicted = await registry.evict_idle(
            idle_seconds=1.0,
            now=datetime.now(UTC) + timedelta(seconds=2),
            deadline=asyncio.get_running_loop().time() + 30,
        )
        assert key in evicted
        await _wait_cleanup(cleanup)
        idle_result = await successful_turn("P53 after idle eviction", 14)
        assert idle_result.terminal_type == "RunCompleted"
        new_idle = registry.get(key)
        assert new_idle is not None and new_idle is not before_idle
        assert before_idle.closed
        after_idle_binding = await resources.bindings.get(session_id)
        after_idle_sandbox = _sandbox_id(after_idle_binding)
        assert after_idle_sandbox is not None
        scenarios["idle_eviction"] = _scenario_record(
            name="idle_eviction",
            before=before_idle,
            after=new_idle,
            result=idle_result,
            history_count=9,
            failed_markers_absent=True,
            provider_state=getattr(after_idle_binding, "provider_state", None),
            provider_before=before_idle_sandbox,
            provider_after=after_idle_sandbox,
            provider_before_state=getattr(before_idle_binding, "provider_state", None),
        )

        assert len(factory.created) == 8
        assert action._index == len(action._responses)
        receipt = {
            "schema": _SCHEMA,
            "candidate": {**identity, "tracked_tree_clean": True},
            "generated_at": datetime.now(UTC).isoformat(),
            "run_id": os.environ[_RUN_ID_ENV],
            "continuity": scenarios.pop("resident_continuity"),
            "rotations": scenarios,
            "assertions": {
                "all_required_rotations": set(scenarios)
                == {
                    "timeout",
                    "cancellation",
                    "provider_failure",
                    "claim_loss",
                    "commit_failure",
                    "fingerprint_change",
                    "idle_eviction",
                },
                "history_rehydrated_after_every_rotation": True,
                "failed_python_state_absent_after_tainted_rotation": True,
                "native_rlm_identity_rotated": True,
                "fingerprint_rotation_handoff_preserved_provider_root": True,
            },
            "cleanup": {"confirmed_absent": False, "admission_restored": True},
            "passed": False,
        }
    finally:
        await registry.shutdown(drain_seconds=60)
        await provider.aclose(drain_seconds=60)
        await cleanup.shutdown(drain_seconds=60)
        disposed = False
        for _ in range(120):
            if await resources.adispose(drain_seconds=60):
                disposed = True
                break
            await asyncio.sleep(0.5)
        assert disposed, "P53 Daytona resource cleanup did not settle"
        await engine.dispose()

    receipt["cleanup"] = {"confirmed_absent": True, "admission_restored": True}
    receipt["passed"] = True
    unsigned = {key: value for key, value in receipt.items() if key != "manifest_sha256"}
    receipt["manifest_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _write_receipt(receipt)
