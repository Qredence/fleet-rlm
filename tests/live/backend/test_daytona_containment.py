"""Operator-gated Daytona interpreter-context containment proof.

This is deliberately independent of Fleet's RLM, broker, and lifecycle
machinery. It records the pinned provider's behavior for an ordinary child and
a detached process-session child, then proves whole-sandbox deletion removes
the disposable execution boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from dotenv import load_dotenv

from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, build_daytona_client
from fleet_rlm.daytona.provisioning import sandbox_spec_from_settings

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(300)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_ENV = "FLEET_PHASE1_CONTAINMENT_EVIDENCE_PATH"
_REQUIRED_DSPY = "3.3.1"
_REQUIRED_DAYTONA = "0.210.0"


def _load_live_settings() -> Any:
    load_dotenv(_REPO_ROOT / ".env", override=False)
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for the Daytona containment lane")
    settings = load_runtime_settings()
    if settings.daytona_api_key is None:
        pytest.fail("Daytona containment lane requires the configured Daytona API key")
    if importlib.metadata.version("dspy") != _REQUIRED_DSPY:
        pytest.fail("Daytona containment lane requires the pinned DSPy release")
    if importlib.metadata.version("daytona") != _REQUIRED_DAYTONA:
        pytest.fail("Daytona containment lane requires the pinned Daytona release")
    return settings


def _write_receipt(payload: dict[str, object]) -> None:
    """Atomically write an optional bounded, non-secret operator receipt."""
    raw_path = os.environ.get(_EVIDENCE_ENV)
    if not raw_path:
        return
    path = Path(raw_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


async def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        with contextlib.suppress(BaseException):
            result = close()
            if hasattr(result, "__await__"):
                await result


async def _delete_sandbox(platform: LiveDaytonaPlatform, sandbox: Any) -> bool:
    """Delete one disposable sandbox and confirm provider absence."""
    sandbox_id = str(getattr(sandbox, "id", ""))
    with contextlib.suppress(BaseException):
        await platform.stop(sandbox_id, timeout=60, force=True)
    with contextlib.suppress(BaseException):
        await platform.delete(sandbox)
    with contextlib.suppress(BaseException):
        return await platform.get(sandbox_id) is None
    return False


@pytest.mark.asyncio
async def test_daytona_interpreter_context_process_containment() -> None:
    """Record whether deleting a context contains normal and detached children."""
    settings = _load_live_settings()
    client = build_daytona_client(settings)
    platform = LiveDaytonaPlatform(client, sandbox_spec_from_settings(settings))
    sandbox: Any | None = None
    context: Any | None = None
    ordinary_marker = f"/tmp/fleet-p1-ordinary-{uuid4().hex}.txt"
    detached_marker = f"/tmp/fleet-p1-detached-{uuid4().hex}.txt"
    context_deleted = False
    sandbox_absent = False
    ordinary_child_completed = False
    detached_child_contained = False
    started = time.perf_counter()

    try:
        sandbox = await platform.create(with_volume=False, ephemeral=True)
        context = await sandbox.code_interpreter.create_context(cwd="/home/daytona", request_timeout=60)
        ordinary_code = f"open({ordinary_marker!r}, 'w').write('ordinary')"
        detached_code = f"import time; time.sleep(3); open({detached_marker!r}, 'w').write('detached')"
        spawn_code = (
            "import subprocess, sys\n"
            f"subprocess.run([sys.executable, '-c', {ordinary_code!r}], check=True)\n"
            f"subprocess.Popen([sys.executable, '-c', {detached_code!r}], start_new_session=True)\n"
            "print('spawned')"
        )
        result = await sandbox.code_interpreter.run_code(spawn_code, context=context, timeout=60)
        assert getattr(result, "error", None) is None
        ordinary = await sandbox.process.code_run(f"import os; print(os.path.exists({ordinary_marker!r}))", timeout=20)
        ordinary_child_completed = str(getattr(ordinary, "result", "")).strip() == "True"
        assert ordinary_child_completed

        # `delete_context` is the only pinned SDK interpreter-context cleanup
        # mechanism. The detached marker is checked after its delay so this
        # proof can conclusively show whether it controls a process session.
        await sandbox.code_interpreter.delete_context(context, request_timeout=60)
        context_deleted = True
        await asyncio.sleep(4)
        detached = await sandbox.process.code_run(f"import os; print(os.path.exists({detached_marker!r}))", timeout=20)
        detached_child_contained = str(getattr(detached, "result", "")).strip() == "False"
    finally:
        if sandbox is not None:
            sandbox_absent = await _delete_sandbox(platform, sandbox)
        await _close_client(client)

    _write_receipt(
        {
            "schema": "fleet.daytona-interpreter-containment/v1",
            "versions": {
                "python": sys.version.split()[0],
                "dspy": importlib.metadata.version("dspy"),
                "daytona": importlib.metadata.version("daytona"),
            },
            "candidate": "code_interpreter.delete_context",
            "ordinary_child_completed": ordinary_child_completed,
            "context_deleted": context_deleted,
            "detached_child_contained": detached_child_contained,
            "sandbox_absent_confirmed": sandbox_absent,
            "elapsed_ms": max(0, int((time.perf_counter() - started) * 1000)),
        }
    )
    assert context_deleted
    assert sandbox_absent, "disposable sandbox deletion must be confirmed"
