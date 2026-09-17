"""Ephemeral interpreter provisioning contracts."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.provisioning import acquire_ephemeral_interpreter


def _patch_acquire_dependencies(
    *,
    platform: MagicMock,
    provisioner: MagicMock,
    interpreter: MagicMock | None = None,
) -> tuple[MagicMock, ...]:
    return (
        patch("fleet_rlm.daytona.platform.build_daytona_client", return_value=MagicMock()),
        patch("fleet_rlm.daytona.provisioning.sandbox_spec_from_settings", return_value=MagicMock()),
        patch("fleet_rlm.daytona.platform.LiveDaytonaPlatform", return_value=platform),
        patch("fleet_rlm.daytona.platform.LiveDaytonaVolumeClient", return_value=MagicMock()),
        patch(
            "fleet_rlm.daytona.provisioning.volume_config_from_settings",
            return_value=MagicMock(paths=MagicMock(return_value=MagicMock(mount_path="/home/daytona/fleet"))),
        ),
        patch("fleet_rlm.daytona.provisioning.SandboxProvisioner", return_value=provisioner),
        patch("fleet_rlm.daytona.provisioning.get_or_create_volume_id", AsyncMock(return_value="volume-1")),
        patch("fleet_rlm.daytona.platform.sandbox_state", return_value="running"),
        patch(
            "fleet_rlm.daytona.interpreter.DaytonaCodeInterpreter",
            return_value=interpreter or MagicMock(),
        ),
        patch("fleet_rlm.daytona.interpreter.sandbox_backend", return_value=MagicMock()),
    )


@pytest.mark.asyncio
async def test_acquire_ephemeral_interpreter_does_not_probe_execute() -> None:
    settings = MagicMock()
    sandbox = MagicMock(id="sandbox-1")
    platform = MagicMock()
    platform.start = AsyncMock()
    platform.get = AsyncMock(return_value=sandbox)
    provisioner = MagicMock()
    provisioner.create = AsyncMock(return_value=sandbox)
    provisioner.expected_mount = MagicMock(return_value=MagicMock())
    provisioner.verify_run_layout = AsyncMock()
    interpreter = MagicMock()
    interpreter.execute = MagicMock()

    patches = _patch_acquire_dependencies(platform=platform, provisioner=provisioner, interpreter=interpreter)
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        lease = await acquire_ephemeral_interpreter(settings, purpose="test", workspace_id=uuid4())

    interpreter.execute.assert_not_called()
    assert lease.interpreter is interpreter


@pytest.mark.asyncio
async def test_acquire_ephemeral_interpreter_uses_configured_execution_timeout() -> None:
    """The ephemeral lease must honor rlm.execution_timeout_s, not the 120s default."""
    settings = MagicMock()
    settings.rlm_execution_timeout_s = 300
    sandbox = MagicMock(id="sandbox-3")
    platform = MagicMock()
    platform.start = AsyncMock()
    platform.get = AsyncMock(return_value=sandbox)
    provisioner = MagicMock()
    provisioner.create = AsyncMock(return_value=sandbox)
    provisioner.expected_mount = MagicMock(return_value=MagicMock())
    provisioner.verify_run_layout = AsyncMock()
    backend_calls: list[dict[str, object]] = []

    patches = _patch_acquire_dependencies(platform=platform, provisioner=provisioner)
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        stack.enter_context(
            patch(
                "fleet_rlm.daytona.interpreter.sandbox_backend",
                side_effect=lambda *_args, **kwargs: backend_calls.append(kwargs) or MagicMock(),
            )
        )
        await acquire_ephemeral_interpreter(settings, purpose="test", workspace_id=uuid4())

    assert backend_calls and backend_calls[0]["timeout_s"] == 300


@pytest.mark.asyncio
async def test_acquire_ephemeral_interpreter_retires_sandbox_when_layout_fails() -> None:
    settings = MagicMock()
    sandbox = MagicMock(id="sandbox-2")
    platform = MagicMock()
    platform.delete = AsyncMock()
    provisioner = MagicMock()
    provisioner.create = AsyncMock(return_value=sandbox)
    provisioner.expected_mount = MagicMock(return_value=MagicMock())
    provisioner.verify_run_layout = AsyncMock(
        side_effect=DaytonaAdapterError(message="layout", cause_type="LayoutError")
    )

    patches = _patch_acquire_dependencies(platform=platform, provisioner=provisioner)
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        with pytest.raises(DaytonaAdapterError, match="layout"):
            await acquire_ephemeral_interpreter(settings, purpose="test", workspace_id=uuid4())

    platform.delete.assert_awaited_once_with(sandbox)


def test_bind_context_manifest_rejects_after_broker_startup() -> None:
    broker = DaytonaHttpToolBroker(sandbox=MagicMock())
    broker._broker_url = "http://example"
    with pytest.raises(DaytonaAdapterError, match="must be bound before broker startup"):
        broker.bind_context_manifest(
            trusted_mount_root="/home/daytona/fleet",
            expected_manifest_sha256="abc123",
        )
