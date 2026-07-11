"""Opt-in live kernel proofs: Daytona path + optional full RLM.

Gate: FLEET_CLEAN_LIVE=1 (or FLEET_RLM_RUN_LIVE_LLM_TESTS=1)

Requires:
- Daytona: DAYTONA_API_KEY or FLEET_CLEAN_DAYTONA_API_KEY
- Full RLM: also OPENAI_API_KEY / FLEET_CLEAN_LLM_API_KEY that can reach a chat model
  (set FLEET_CLEAN_LLM_BASE_URL if using an OpenAI-compatible gateway)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from fleet_rlm_clean.api.sse import SSEProjector
from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.live_context import LiveKernelResources
from fleet_rlm_clean.chat.turn_coordinator import TurnCoordinator
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.rlm.runner import RLMRunner

pytestmark = [pytest.mark.live_daytona]


def _live_enabled() -> bool:
    return os.environ.get("FLEET_CLEAN_LIVE", "").strip() in {"1", "true", "yes"} or os.environ.get(
        "FLEET_RLM_RUN_LIVE_LLM_TESTS", ""
    ).strip() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("DAYTONA_API_KEY") or os.environ.get("FLEET_CLEAN_DAYTONA_API_KEY"))


def _have_llm() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("FLEET_CLEAN_LLM_API_KEY")
        or os.environ.get("LLM_API_KEY")
    )


def _skip_unless_live_daytona() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_CLEAN_LIVE=1 for live kernel tests")
    if not _have_daytona():
        pytest.skip("DAYTONA_API_KEY / FLEET_CLEAN_DAYTONA_API_KEY not configured")


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


def _write_evidence(name: str, payload: dict) -> Path:
    evidence_dir = Path(".scratch/clean-backend/assets")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_live_session_manager_interpreter_on_volume() -> None:
    """Real Daytona: acquire lease, execute Python, release (never delete mid-run)."""
    _skip_unless_live_daytona()

    resources = LiveKernelResources(Settings())
    command = ChatTurnCommand(
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        message="probe",
    )
    try:
        context = await resources.build_context(command)
        assert context.lease.sandbox_id
        assert context.lease.volume_id
        assert context.lease.mount_path == "/home/daytona/fleet"
        context.lease.interpreter.start()
        context.lease.interpreter.execute("fleet_live_marker = 17 * 19")
        printed = context.lease.interpreter.execute("print(fleet_live_marker)")
        assert "323" in printed
        await resources.session_manager.release(context.lease)
        # Second release is idempotent and must not imply sandbox delete.
        await resources.session_manager.release(context.lease)

        path = _write_evidence(
            "live-daytona-session-manager-evidence.json",
            {
                "commit": _git_commit(),
                "uv_lock_sha256_16": _lockfile_fingerprint(),
                "sandbox_id": context.lease.sandbox_id,
                "volume_id": context.lease.volume_id,
                "mount_path": context.lease.mount_path,
                "python_product": "323",
                "release_idempotent": True,
            },
        )
        assert path.exists()
    finally:
        resources.cleanup()


@pytest.mark.asyncio
async def test_live_kernel_rlm_daytona_through_runner() -> None:
    """Full path: runner + real RLM + Daytona (needs working chat LM)."""
    _skip_unless_live_daytona()
    if not _have_llm():
        pytest.skip("LLM API key not configured")

    resources = LiveKernelResources(Settings())
    command = ChatTurnCommand(
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        message=(
            "Task: in the Python REPL, evaluate 17*19 and print it. "
            "Then call llm_query('reply with only ok') once. "
            "Finally call final_answer with the product 323 as the answer field."
        ),
    )
    try:
        context = await resources.build_context(command)
        assert context.models.sub_lm is not None
        context.lease.interpreter.start()
        context.lease.interpreter.execute("fleet_live_marker = 17 * 19")
        printed = context.lease.interpreter.execute("print(fleet_live_marker)")
        assert "323" in printed

        # Probe sub LM before long RLM loop.
        try:
            sub_out = context.models.sub_lm("Reply with exactly the word ok and nothing else.")
            sub_text = str(sub_out)
            llm_ok = True
        except Exception as exc:  # noqa: BLE001 - record and skip full RLM
            path = _write_evidence(
                "live-kernel-evidence-partial.json",
                {
                    "commit": _git_commit(),
                    "uv_lock_sha256_16": _lockfile_fingerprint(),
                    "daytona_ok": True,
                    "llm_ok": False,
                    "llm_error_type": type(exc).__name__,
                    "llm_error": str(exc)[:240],
                    "interpreter_python_product": "323",
                    "sandbox_id": context.lease.sandbox_id,
                    "volume_id": context.lease.volume_id,
                    "hint": "Set FLEET_CLEAN_LLM_BASE_URL to a working OpenAI-compatible endpoint "
                    "and a valid chat model via FLEET_CLEAN_ROOT_MODEL / FLEET_CLEAN_SUB_MODEL.",
                },
            )
            pytest.skip(f"LLM not usable for full RLM proof (see {path}): {type(exc).__name__}")

        runner = RLMRunner()
        events = [event async for event in runner.stream(context)]
        kinds = [e.kind.value for e in events]
        assert kinds[0] == "run.started"
        assert kinds[-1] in {"run.completed", "error"}
        assert sum(1 for k in kinds if k in {"run.completed", "error"}) == 1

        # Coordinator/SSE projection parity
        frames = list(SSEProjector().project(events))
        assert frames[0].startswith("data: ")

        path = _write_evidence(
            "live-kernel-evidence.json",
            {
                "commit": _git_commit(),
                "uv_lock_sha256_16": _lockfile_fingerprint(),
                "daytona_ok": True,
                "llm_ok": llm_ok,
                "event_kinds": kinds,
                "terminal": events[-1].kind.value,
                "terminal_payload_keys": sorted(events[-1].payload.keys()),
                "interpreter_python_product": "323",
                "sub_lm_invoked": True,
                "sub_lm_sample": sub_text[:80],
                "sandbox_id": context.lease.sandbox_id,
                "volume_id": context.lease.volume_id,
                "sse_frames": len(frames),
            },
        )
        assert path.exists()
    finally:
        resources.cleanup()


@pytest.mark.asyncio
async def test_live_coordinator_streams_terminal() -> None:
    """TurnCoordinator with live context; skips if LLM unusable."""
    _skip_unless_live_daytona()
    if not _have_llm():
        pytest.skip("LLM API key not configured")

    resources = LiveKernelResources(Settings())
    command = ChatTurnCommand(
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        message="Submit the answer 'pong'. Keep the trajectory short.",
    )
    try:
        context = await resources.build_context(command)
        try:
            context.models.sub_lm("ok")
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"LLM not usable: {type(exc).__name__}")

        def sync_builder(cmd: ChatTurnCommand):
            return context

        coordinator = TurnCoordinator(runner=RLMRunner(), context_builder=sync_builder)
        events = [e async for e in coordinator.stream(command)]
        assert events[0].kind.value == "run.started"
        assert events[-1].kind.value in {"run.completed", "error"}
    finally:
        resources.cleanup()
