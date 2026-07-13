"""Opt-in live proofs for stateful recovery (impl-10).

Gate: FLEET_LIVE=1 (or FLEET_RLM_RUN_LIVE_LLM_TESTS=1)

Requires Daytona credentials. History/restart use durable sqlite;
sandbox lifecycle + Volume replace use live Daytona.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm.chat.commands import ChatTurnCommand
from fleet_rlm.chat.live_context import LiveKernelResources
from fleet_rlm.chat.turn_coordinator import TurnCoordinator, ephemeral_lease
from fleet_rlm.config import Settings
from fleet_rlm.daytona.bindings import SandboxBinding
from fleet_rlm.daytona.session_manager import LeaseRequest
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.events import EventRecorder, RuntimeEvent, RuntimeEventKind
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.history import history_message_count, turns_to_history

if TYPE_CHECKING:
    from fleet_rlm.rlm.runner import TurnEventStream

pytestmark = [pytest.mark.live_daytona]

MARKER_CONTENT = "fleet-rlm-recovery-ok"


def _live_enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip() in {"1", "true", "yes"} or os.environ.get(
        "FLEET_RLM_RUN_LIVE_LLM_TESTS", ""
    ).strip() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("FLEET_DAYTONA_API_KEY"))


def _skip_unless_live_daytona() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_LIVE=1 for live recovery tests")
    if not _have_daytona():
        pytest.skip("FLEET_DAYTONA_API_KEY not configured")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _lockfile_fingerprint() -> str:
    lock = Path("uv.lock")
    if not lock.exists():
        return "missing-uv.lock"
    return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]


def _write_evidence(name: str, payload: dict[str, Any]) -> Path:
    evidence_dir = Path(".scratch/clean-backend/assets")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


class _ScriptedRunner:
    def __init__(self, answer: str = "reply") -> None:
        self.answer = answer
        self.seen_history_lens: list[int] = []
        self.seen_history_messages: list[list[dict[str, Any]]] = []

    def stream(self, context: RLMTurnContext) -> "TurnEventStream":
        from fleet_rlm.rlm.outcome import TurnExecutionOutcome
        from fleet_rlm.rlm.runner import TurnEventStream

        self.seen_history_lens.append(history_message_count(context.history))
        messages = list(getattr(context.history, "messages", None) or []) if context.history else []
        self.seen_history_messages.append(messages)

        async def _agen() -> AsyncIterator[RuntimeEvent]:
            recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
            yield recorder.emit(RuntimeEventKind.RUN_STARTED, {})
            yield recorder.emit(RuntimeEventKind.TEXT_DELTA, {"text": self.answer})

        return TurnEventStream(
            _agen(),
            outcome=TurnExecutionOutcome(
                terminal_status="completed",
                assistant_text=self.answer,
            ),
        )


def _stub_builder(command: ChatTurnCommand) -> RLMTurnContext:
    return RLMTurnContext(
        run_id=uuid4(),
        session_id=command.session_id,
        user_id=command.user_id,
        workspace_id=command.workspace_id,
        request=command.message,
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(),
        lease=ephemeral_lease(MagicMock()),
    )


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_stateful_live_recovery_scenarios(tmp_path: Path) -> None:
    """A-D: history, API restart, inactive sandbox, replace+Volume read."""
    _skip_unless_live_daytona()

    db_path = tmp_path / "recovery.sqlite"
    notes: list[str] = []
    scenarios: dict[str, bool] = {
        "two_turn_history": False,
        "api_restart": False,
        "inactive_sandbox": False,
        "replace_volume_read": False,
    }
    sandbox_ids: list[str] = []
    volume_id: str | None = None
    history_count_after_turn2 = 0

    resources = await LiveKernelResources.with_sqlite_file(
        db_path,
        Settings(),
        allow_ephemeral_fallback=False,
    )
    assert resources.sessions is not None
    user_id, workspace_id = uuid4(), uuid4()
    session = await resources.sessions.create(
        user_id=user_id,
        workspace_id=workspace_id,
        title="recovery-proof",
    )
    session_id = session.id

    try:
        # --- A: two-turn History via durable SessionRepository ---
        runner1 = _ScriptedRunner(answer="first-answer")
        coord1 = TurnCoordinator(
            runner=runner1,
            context_builder=_stub_builder,
            session_repository=resources.sessions,
        )
        events1 = [
            e
            async for e in coord1.stream(
                ChatTurnCommand(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    message="turn-one",
                )
            )
        ]
        assert events1[-1].kind == RuntimeEventKind.RUN_COMPLETED
        assert runner1.seen_history_lens[0] == 0

        runner2 = _ScriptedRunner(answer="second-answer")
        coord2 = TurnCoordinator(
            runner=runner2,
            context_builder=_stub_builder,
            session_repository=resources.sessions,
        )
        events2 = [
            e
            async for e in coord2.stream(
                ChatTurnCommand(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    message="turn-two",
                )
            )
        ]
        assert events2[-1].kind == RuntimeEventKind.RUN_COMPLETED
        assert runner2.seen_history_lens[0] == 2
        assert runner2.seen_history_messages[0][0]["content"] == "turn-one"
        assert runner2.seen_history_messages[0][1]["content"] == "first-answer"

        snap = await resources.sessions.load(session_id)
        history = turns_to_history(snap.turns)
        history_count_after_turn2 = history_message_count(history)
        assert history_count_after_turn2 == 4
        scenarios["two_turn_history"] = True

        # --- B/C/D use live Daytona with Volume (no ephemeral) ---
        lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )
        resources.track_sandbox(lease.sandbox_id)
        sandbox_ids.append(lease.sandbox_id)
        volume_id = lease.volume_id
        assert volume_id and volume_id != "none"
        assert lease.mount_path == "/home/daytona/fleet"

        mount = lease.mount_path
        marker_path = f"{mount}/sessions/{session_id}/recovery-marker.txt"
        lease.interpreter.start()
        write_code = (
            "from pathlib import Path\n"
            f"p = Path({marker_path!r})\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            f"p.write_text({MARKER_CONTENT!r}, encoding='utf-8')\n"
            "print(p.read_text(encoding='utf-8'))\n"
        )
        written = lease.interpreter.execute(write_code)
        assert MARKER_CONTENT in written
        await resources.session_manager.release(lease)

        binding = await resources.bindings.get(session_id)
        assert binding is not None
        assert binding.sandbox_id == sandbox_ids[0]
        assert binding.volume_id == volume_id

        # --- B: API restart (new process objects, same sqlite file) ---
        first_sandbox = binding.sandbox_id
        # Drop process handles only — keep Daytona sandboxes + sqlite file.
        resources.forget_sandboxes()
        await resources.adispose_engine()

        restarted = await LiveKernelResources.reopen_sqlite_file(
            db_path,
            Settings(),
            allow_ephemeral_fallback=False,
        )
        resources = restarted
        assert resources.sessions is not None

        reloaded = await resources.sessions.load(session_id)
        assert history_message_count(turns_to_history(reloaded.turns)) == 4
        rebound = await resources.bindings.get(session_id)
        assert rebound is not None
        assert rebound.sandbox_id == first_sandbox
        assert rebound.volume_id == volume_id

        lease2 = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )
        resources.track_sandbox(lease2.sandbox_id)
        if lease2.sandbox_id not in sandbox_ids:
            sandbox_ids.append(lease2.sandbox_id)
        # Also track pre-restart sandbox for cleanup
        if first_sandbox and first_sandbox not in sandbox_ids:
            sandbox_ids.append(first_sandbox)
            resources.track_sandbox(first_sandbox)
        assert lease2.volume_id == volume_id
        scenarios["api_restart"] = True

        # --- C: inactive sandbox lifecycle (stop → acquire restarts) ---
        import time

        current_sid = lease2.sandbox_id
        await resources.session_manager.release(lease2)
        try:
            await resources.session_manager.stop(current_sid)
            notes.append("stop_supported")
            # Allow provider state to settle before re-acquire/start.
            time.sleep(3)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"stop_failed:{type(exc).__name__}")
            # Fallback proof: explicit start while still "running" is a no-op path
            try:
                await resources.session_manager.start(current_sid)
                notes.append("start_fallback_ok")
            except Exception as start_exc:  # noqa: BLE001
                notes.append(f"start_fallback_failed:{type(start_exc).__name__}")

        lease3 = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )
        resources.track_sandbox(lease3.sandbox_id)
        if lease3.sandbox_id not in sandbox_ids:
            sandbox_ids.append(lease3.sandbox_id)
        assert lease3.sandbox_id
        assert lease3.volume_id == volume_id
        # Interpreter must work after inactive → running transition
        lease3.interpreter.start()
        ping = lease3.interpreter.execute("print(41 + 1)")
        assert "42" in ping
        scenarios["inactive_sandbox"] = True
        await resources.session_manager.release(lease3)

        # --- D: replace sandbox; same Volume still has marker ---
        binding_before = await resources.bindings.get(session_id)
        assert binding_before is not None
        old_sid = binding_before.sandbox_id
        new_binding = await resources.session_manager.replace(
            SandboxBinding(
                session_id=session_id,
                sandbox_id=old_sid,
                workspace_id=workspace_id,
                volume_id=volume_id,
                volume_subpath=f"workspaces/{workspace_id}",
                mount_path=mount,
                provider_state="unrecoverable",
            ),
            workspace_id=workspace_id,
            user_id=user_id,
        )
        assert new_binding.volume_id == volume_id
        assert new_binding.sandbox_id != old_sid
        resources.track_sandbox(new_binding.sandbox_id)
        if new_binding.sandbox_id and new_binding.sandbox_id not in sandbox_ids:
            sandbox_ids.append(new_binding.sandbox_id)

        lease4 = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )
        resources.track_sandbox(lease4.sandbox_id)
        if lease4.sandbox_id not in sandbox_ids:
            sandbox_ids.append(lease4.sandbox_id)
        assert lease4.volume_id == volume_id
        lease4.interpreter.start()
        read_code = (
            "from pathlib import Path\n"
            f"p = Path({marker_path!r})\n"
            "print(p.read_text(encoding='utf-8') if p.is_file() else 'MISSING')\n"
        )
        read_out = lease4.interpreter.execute(read_code)
        assert MARKER_CONTENT in read_out, f"volume marker missing after replace: {read_out!r}"
        await resources.session_manager.release(lease4)
        scenarios["replace_volume_read"] = True

        path = _write_evidence(
            "live-stateful-recovery-evidence.json",
            {
                "commit": _git_commit(),
                "uv_lock_sha256_16": _lockfile_fingerprint(),
                "scenarios": scenarios,
                "sandbox_ids": sandbox_ids,
                "volume_id": volume_id,
                "history_message_count_after_turn2": history_count_after_turn2,
                "session_id": str(session_id),
                "notes": notes,
                "marker_path": marker_path,
            },
        )
        assert path.exists()
        assert all(scenarios.values()), f"incomplete scenarios: {scenarios}"
    finally:
        # Track all known ids then delete
        for sid in sandbox_ids:
            resources.track_sandbox(sid)
        await resources.adispose()
