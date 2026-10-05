"""Sandbox provider lifecycle and binding-repository contracts.

* ``test_sandbox_lifecycle.py``: B8: provider get/state matrix and root Sandbox retention defaults.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from daytona.common.errors import DaytonaQueueTimeoutError, DaytonaSpotEvictedError

from fleet_rlm.daytona.errors import (
    DEFAULT_SANITIZED_FAILURE_MAX_CHARS,
    ProviderRequestError,
    classify_provider_error,
    is_sandbox_not_found,
    is_transient_provider_failure,
    map_provider_error,
    provider_status_category,
    sanitize_failure_text,
    sanitize_provider_message,
)
from fleet_rlm.daytona.runtime import (
    DaytonaSandboxSpec,
    LiveDaytonaPlatform,
    LiveDaytonaVolumeClient,
    normalize_state,
)

# --- from test_sandbox_lifecycle.py -----------------------------------
_SPEC = DaytonaSandboxSpec("fleet-test-v1")


class DaytonaNotFoundError(Exception):
    """Name-matched stand-in for the SDK not-found type."""

    def __init__(self, message: str = "missing", status_code: int = 404) -> None:
        super().__init__(message)
        self.status_code = status_code


class _AuthError(Exception):
    def __init__(self) -> None:
        super().__init__("unauthorized")
        self.status_code = 401


def test_normalize_state_unknown_is_unrecoverable() -> None:
    assert normalize_state("running") == "running"
    assert normalize_state("stopped") == "stopped"
    assert normalize_state("error") == "unrecoverable"
    assert normalize_state("booting") == "unrecoverable"
    assert normalize_state("weird-positive") == "unrecoverable"
    assert normalize_state(None) == "missing"
    assert normalize_state("") == "missing"


def test_is_sandbox_not_found_only_for_explicit_missing() -> None:
    assert is_sandbox_not_found(DaytonaNotFoundError()) is True
    assert is_sandbox_not_found(_AuthError()) is False
    assert is_sandbox_not_found(TimeoutError("timed out")) is False
    assert is_sandbox_not_found(RuntimeError("boom")) is False


def test_map_provider_error_non_missing_is_provider_request_error() -> None:
    mapped = map_provider_error(_AuthError())
    assert isinstance(mapped, ProviderRequestError)
    assert mapped.cause_type == "_AuthError"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_AuthError(), "auth"),
        (SimpleNamespace(status_code=429), "quota"),
        (
            ProviderRequestError(
                "Total disk limit exceeded. Upgrade your organization's Tier.",
                cause_type="DaytonaValidationError",
                status_code=400,
            ),
            "quota",
        ),
        (TimeoutError("slow"), "timeout"),
        (ConnectionError("offline"), "network"),
        (SimpleNamespace(status_code=503), "provider_5xx"),
        (SimpleNamespace(status_code=422), "request_validation"),
        (ProviderRequestError("mount", cause_type="WorkspaceMountMismatch"), "mount_mismatch"),
        (ProviderRequestError("interp", cause_type="InterpreterLifecycleError"), "interpreter"),
        (RuntimeError("other"), "unknown"),
    ],
)
def test_provider_error_classification(exc: object, expected: str) -> None:
    assert classify_provider_error(exc) == expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RuntimeError("model not found"), "unknown"),
        (SimpleNamespace(response=SimpleNamespace(status_code=404)), "request_validation"),
        (SimpleNamespace(response=SimpleNamespace(status_code=401)), "auth"),
        (SimpleNamespace(response=SimpleNamespace(status_code=503)), "provider_5xx"),
        (RuntimeError("404 Not Found: model databricks-deepseek-v4-flash-0731"), "request_validation"),
        (RuntimeError("Error 503: upstream unavailable"), "provider_5xx"),
        (RuntimeError("401 Unauthorized"), "auth"),
        (RuntimeError("429 Too Many Requests"), "quota"),
    ],
)
def test_provider_error_classification_reads_nested_status_and_text(exc: object, expected: str) -> None:
    """404/5xx must classify even without a top-level ``status_code`` attribute."""
    assert classify_provider_error(exc) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [(None, "none"), (401, "4xx"), (429, "4xx"), (503, "5xx")],
)
def test_provider_status_category(status: int | None, expected: str) -> None:
    assert provider_status_category(status) == expected


def test_sanitize_provider_message_redacts_secrets_and_private_paths() -> None:
    sanitized = sanitize_provider_message(
        "api_key=super-secret Bearer private-token at /Users/zach/project/.env and /Volumes/SSD/key.txt"
    )

    assert "super-secret" not in sanitized
    assert "private-token" not in sanitized
    assert "/Users/zach" not in sanitized
    assert "/Volumes/SSD" not in sanitized
    assert sanitized.count("[redacted]") >= 4


def test_sanitize_provider_message_is_bounded_after_redaction() -> None:
    sanitized = sanitize_provider_message("provider failure " + "x" * 1000)

    assert len(sanitized) == DEFAULT_SANITIZED_FAILURE_MAX_CHARS


def test_sanitize_failure_text_types_and_redacts_exception() -> None:
    text = sanitize_failure_text(RuntimeError("provider down api_key=super-secret path=/tmp/private"))

    assert text.startswith("RuntimeError: provider down ")
    assert "super-secret" not in text
    assert "/tmp/private" not in text
    assert text.count("[redacted]") == 2


def test_sanitize_failure_text_caps_after_redaction() -> None:
    text = sanitize_failure_text(RuntimeError("api_key=secret " + "x" * 500))

    assert len(text) == 200
    assert "secret" not in text


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (SimpleNamespace(status_code=503), True),
        (TimeoutError("timed out"), True),
        (ConnectionError("offline"), True),
        (ProviderRequestError("502 Bad Gateway", cause_type="DaytonaError"), True),
        (_AuthError(), False),
        (SimpleNamespace(status_code=422), False),
        (
            ProviderRequestError(
                "Total disk limit exceeded. Upgrade your organization's Tier.",
                cause_type="DaytonaValidationError",
                status_code=400,
            ),
            False,
        ),
        (ProviderRequestError("mount", cause_type="WorkspaceMountMismatch"), False),
        (ProviderRequestError("404 sandbox missing", cause_type="DaytonaError"), False),
        (ProviderRequestError("processed 15001 objects", cause_type="DaytonaError"), False),
        (RuntimeError("other"), False),
    ],
)
def test_is_transient_provider_failure(exc: object, expected: bool) -> None:
    assert is_transient_provider_failure(exc) is expected


@pytest.mark.asyncio
async def test_live_platform_get_none_only_on_not_found() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=DaytonaNotFoundError())
    platform = LiveDaytonaPlatform(client, _SPEC)
    assert await platform.get("sb-missing") is None


@pytest.mark.asyncio
async def test_live_platform_stop_treats_missing_sandbox_as_already_stopped() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=DaytonaNotFoundError())
    client.stop = AsyncMock()
    client.delete = AsyncMock()
    platform = LiveDaytonaPlatform(client, _SPEC)

    await platform.stop("sb-missing", force=True)

    client.stop.assert_not_awaited()
    client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_platform_get_raises_on_auth_error() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=_AuthError())
    platform = LiveDaytonaPlatform(client, _SPEC)
    with pytest.raises(ProviderRequestError):
        await platform.get("sb-1")


@pytest.mark.asyncio
async def test_live_platform_passes_strict_ephemeral_network_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Params:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    class _Client:
        async def create(self, params: object) -> object:
            return params

    monkeypatch.setattr("daytona.CreateSandboxFromSnapshotParams", _Params)
    platform = LiveDaytonaPlatform(_Client(), _SPEC)
    await platform.create(
        with_volume=False,
        ephemeral=True,
        network_block_all=True,
        network_allow_list="10.0.0.0/8",
        domain_allow_list="gateway.example.test",
    )

    assert captured["volumes"] is None
    assert captured["ephemeral"] is True
    assert captured["network_block_all"] is True
    assert captured["network_allow_list"] == "10.0.0.0/8"
    assert captured["domain_allow_list"] == "gateway.example.test"
    assert captured["auto_stop_interval"] is None
    assert captured["auto_delete_interval"] is None


@pytest.mark.asyncio
async def test_live_volume_client_waits_for_created_volume_to_be_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.volume.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="vol-1", state="creating"),
            SimpleNamespace(id="vol-1", state="ready"),
        ]
    )

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("fleet_rlm.daytona.runtime.asyncio.sleep", _no_sleep)
    volume = await LiveDaytonaVolumeClient(client).get("vol-1", create=True)
    assert volume.state == "ready"
    assert client.volume.get.call_args_list == [
        (("vol-1",), {"create": True}),
        (("vol-1",), {"create": False}),
    ]


@pytest.mark.asyncio
async def test_live_volume_client_rejects_failed_volume_state() -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError

    client = MagicMock()
    client.volume.get = AsyncMock(side_effect=[SimpleNamespace(id="vol-1", state="error")])

    with pytest.raises(DaytonaAdapterError, match="did not become ready"):
        await LiveDaytonaVolumeClient(client).get("vol-1", create=True)


@pytest.mark.asyncio
async def test_live_platform_delete_resolves_id_for_daytona_async_contract() -> None:
    client = MagicMock()
    sandbox = SimpleNamespace(id="sb-1")
    client.get = AsyncMock(return_value=sandbox)
    client.delete = AsyncMock()

    await LiveDaytonaPlatform(client, _SPEC).delete("sb-1")

    client.get.assert_called_once_with("sb-1")
    client.delete.assert_called_once_with(sandbox)


@pytest.mark.asyncio
async def test_live_platform_start_and_stop_use_async_client_methods() -> None:
    client = MagicMock()
    sandbox = MagicMock()
    client.get = AsyncMock(return_value=sandbox)
    client.start = AsyncMock()
    client.stop = AsyncMock()
    platform = LiveDaytonaPlatform(client, _SPEC)

    await platform.start("sb-1")
    await platform.stop("sb-1", timeout=12, force=True)

    assert client.get.await_args_list == [(("sb-1",), {}), (("sb-1",), {})]
    client.start.assert_awaited_once_with(sandbox)
    client.stop.assert_awaited_once_with(sandbox, timeout=12)


@pytest.mark.asyncio
async def test_live_platform_create_start_and_stop_normalize_provider_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Params:
        def __init__(self, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr("daytona.CreateSandboxFromSnapshotParams", _Params)
    client = MagicMock()
    client.create = AsyncMock(side_effect=_AuthError())
    client.get = AsyncMock(side_effect=_AuthError())
    platform = LiveDaytonaPlatform(client, _SPEC)

    with pytest.raises(ProviderRequestError):
        await platform.create(with_volume=False)
    with pytest.raises(ProviderRequestError):
        await platform.start("sb-1")
    with pytest.raises(ProviderRequestError):
        await platform.stop("sb-1")


@pytest.mark.asyncio
async def test_live_platform_force_stop_deletes_sandbox_when_stop_fails() -> None:
    client = MagicMock()
    sandbox = MagicMock()
    client.get = AsyncMock(return_value=sandbox)
    client.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    client.delete = AsyncMock()

    await LiveDaytonaPlatform(client, _SPEC).stop("sb-1", force=True)

    client.delete.assert_awaited_once_with(sandbox)


@pytest.mark.asyncio
async def test_live_platform_create_defaults_ephemeral_false(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Params:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _Mount:
        def __init__(self, **kwargs):
            del kwargs

    import daytona as daytona_mod

    monkeypatch.setattr(daytona_mod, "CreateSandboxFromSnapshotParams", _Params)
    monkeypatch.setattr(daytona_mod, "VolumeMount", _Mount)

    client = MagicMock()
    client.create = AsyncMock(side_effect=lambda params: SimpleNamespace(id="sb-new", params=params))
    platform = LiveDaytonaPlatform(client, _SPEC)
    await platform.create(
        volume_id="vol-1",
        mount_path="/home/daytona/fleet",
        volume_subpath="workspaces/11111111-1111-1111-1111-111111111111",
        labels={"workspace_id": "ws"},
    )
    assert captured.get("ephemeral") is False
    client.create.assert_called_once()


@pytest.mark.asyncio
async def test_live_platform_create_matches_daytona_async_payload_contract() -> None:
    """Pin the scoped mount payload consumed by the asynchronous Daytona SDK."""
    client = MagicMock()
    client.create = AsyncMock(side_effect=lambda params: SimpleNamespace(id="sb-new", params=params))
    platform = LiveDaytonaPlatform(client, _SPEC)

    await platform.create(
        volume_id="vol-1",
        mount_path="/home/daytona/fleet",
        volume_subpath="workspaces/11111111-1111-1111-1111-111111111111",
        labels={"purpose": "fleet-daytona-doctor"},
        ephemeral=True,
    )

    params = client.create.call_args.args[0]
    assert params.ephemeral is True
    assert params.snapshot == _SPEC.snapshot
    assert params.os_user == "daytona"
    assert params.labels == {"purpose": "fleet-daytona-doctor"}
    assert len(params.volumes) == 1
    assert params.volumes[0].to_dict() == {
        "volumeId": "vol-1",
        "mountPath": "/home/daytona/fleet",
        "subpath": "workspaces/11111111-1111-1111-1111-111111111111",
    }


# --- from test_sandbox_binding_repository.py --------------------------


_PROVIDER_SANITIZER_CORPUS = (
    ("bearer_header", "Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
    ("bearer_bare", "Bearer abc.def-ghi", "abc.def-ghi"),
    ("keyword_assignment", "api_key=sk-secret", "sk-secret"),
    ("quoted_json", '{"api_key": "secret-value"}', "secret-value"),
    ("private_path", "read /Users/zach/project/.env", "/Users/zach"),
    ("url_query", 'requests.get("http://host/preview?token=abc123")', "abc123"),
)


@pytest.mark.parametrize(
    ("label", "raw", "secret"), _PROVIDER_SANITIZER_CORPUS, ids=[case[0] for case in _PROVIDER_SANITIZER_CORPUS]
)
def test_sanitize_provider_message_corpus_redacts(label: str, raw: str, secret: str) -> None:
    """Pin secret coverage, because this text reaches the model as repair feedback."""
    cleaned = sanitize_provider_message(raw)

    assert secret not in cleaned, f"{label}: {secret!r} survived -> {cleaned!r}"


def test_sanitize_provider_message_keeps_url_trailing_delimiters() -> None:
    """Regression: the secret patterns' greedy ``\\S+`` ate the URL's closing ``")``."""
    cleaned = sanitize_provider_message('requests.get("http://host/preview?token=abc123")')

    assert cleaned == 'requests.get("[redacted-url]")'


# --- Sandbox Spec and Environment Profiles ---
def test_spec_requires_an_immutable_versioned_name() -> None:
    for name in ("", "latest", "fleet-rlm-python313", "fleet-rlm-python313-v0"):
        with pytest.raises(ValueError):
            DaytonaSandboxSpec(name)


def test_spec_builds_non_root_pinned_image_with_toolchain_and_declared_dependencies() -> None:
    from fleet_rlm.daytona.diagnostics import (
        build_snapshot_image,
        snapshot_dependency_sha256,
        snapshot_execution_dependencies,
    )
    from fleet_rlm.daytona.runtime import BASE_IMAGE

    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    dockerfile = build_snapshot_image(spec).dockerfile()
    digest = snapshot_dependency_sha256()

    assert f"FROM {BASE_IMAGE}" in dockerfile
    assert "groupadd --gid 1000 daytona" in dockerfile
    assert "USER daytona" in dockerfile
    assert "PYTHONUNBUFFERED=1" in dockerfile
    assert f"FLEET_SNAPSHOT_DEPENDENCIES_SHA256={digest}" in dockerfile
    assert "WORKDIR /home/daytona" in dockerfile
    assert snapshot_execution_dependencies() == (
        "mpmath==1.4.1",
        "numpy==2.5.1",
        "pandas==3.0.5",
        "beautifulsoup4==4.15.0",
    )
    install_line = "pip install beautifulsoup4==4.15.0 mpmath==1.4.1 numpy==2.5.1 pandas==3.0.5"
    assert install_line in dockerfile
    assert dockerfile.index(install_line) < dockerfile.index("USER daytona")
    assert "apt-get install -y --no-install-recommends git ca-certificates" in dockerfile
    assert dockerfile.index("apt-get install") < dockerfile.index("USER daytona")
    assert "dspy" not in dockerfile


@pytest.mark.asyncio
async def test_live_platform_builds_session_workspace_sdk_mount_offline() -> None:
    from uuid import uuid4

    from fleet_rlm.daytona.runtime import DEFAULT_SNAPSHOT_NAME
    from fleet_rlm.sessions.bindings import session_workspace_volume_subpath

    class _Client:
        params: Any | None = None

        async def create(self, params: Any) -> Any:
            self.params = params
            return params

    workspace_id = uuid4()
    session_id = uuid4()
    client = _Client()
    platform = LiveDaytonaPlatform(client, DaytonaSandboxSpec(DEFAULT_SNAPSHOT_NAME))

    params = await platform.create(
        volume_id="offline-test-volume",
        mount_path="/workspace",
        volume_subpath=session_workspace_volume_subpath(workspace_id, session_id),
    )

    assert params is client.params
    mount = params.volumes[0]
    assert mount.volume_id == "offline-test-volume"
    assert mount.mount_path == "/workspace"
    assert mount.subpath == session_workspace_volume_subpath(workspace_id, session_id)


def test_snapshot_provenance_is_exact() -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.runtime import verify_sandbox_spec

    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    verify_sandbox_spec(SimpleNamespace(snapshot=spec.snapshot), spec)
    with pytest.raises(DaytonaAdapterError, match="snapshot"):
        verify_sandbox_spec(SimpleNamespace(snapshot="fleet-rlm-python313-v2"), spec)


def test_environment_profiles_keep_capacity_and_data_access_separate() -> None:
    from fleet_rlm.daytona.diagnostics import build_snapshot_image, environment_manifest
    from fleet_rlm.daytona.runtime import DaytonaEnvironmentProfile

    spec = DaytonaSandboxSpec("fleet-rlm-python313-v1")
    workspace_spec = DaytonaSandboxSpec(
        "fleet-rlm-python313-v1",
        profile=DaytonaEnvironmentProfile.WORKSPACE_CHILD,
    )
    session = environment_manifest(spec, DaytonaEnvironmentProfile.SESSION)
    semantic = environment_manifest(spec, DaytonaEnvironmentProfile.SEMANTIC_CHILD)
    workspace = environment_manifest(spec, DaytonaEnvironmentProfile.WORKSPACE_CHILD)

    assert session.image_kind == workspace.image_kind == "session-analysis"
    assert session.volume_allowed and workspace.volume_allowed
    assert not session.warm_pool_eligible and not workspace.warm_pool_eligible
    assert session.resources == workspace.resources == (4, 8, 8)
    assert session.profile is DaytonaEnvironmentProfile.SESSION
    assert workspace.profile is DaytonaEnvironmentProfile.WORKSPACE_CHILD
    assert session.image_identity() == workspace.image_identity()
    assert session.digest == workspace.digest
    assert (
        session.compatible_profiles
        == workspace.compatible_profiles
        == (
            DaytonaEnvironmentProfile.SESSION,
            DaytonaEnvironmentProfile.WORKSPACE_CHILD,
        )
    )
    assert semantic.image_kind == "lean-child"
    assert semantic.dependencies == ()
    semantic_image = build_snapshot_image(
        DaytonaSandboxSpec(
            "fleet-child-test-v1", cpu=2, memory_gib=4, disk_gib=4, profile=DaytonaEnvironmentProfile.SEMANTIC_CHILD
        )
    ).dockerfile()
    assert "pip install" not in semantic_image
    assert "dspy" not in semantic_image
    assert not semantic.volume_allowed and semantic.warm_pool_eligible
    assert semantic.resources == (2, 4, 4)
    assert semantic.digest != session.digest
    assert build_snapshot_image(spec).dockerfile() == build_snapshot_image(workspace_spec).dockerfile()


# --- Deletion Lifecycle and Absence Confirmation ---
@dataclass
class _FakeDeletionSandbox:
    state: str


@dataclass
class _FakeDeletionProvider:
    seen_deletes: list[str] = field(default_factory=list)
    target: _FakeDeletionSandbox | None = None
    deleted: bool = False

    async def delete(self, sandbox_id: str) -> None:
        self.seen_deletes.append(sandbox_id)

    async def get(self, _sandbox_id: str) -> Any | None:
        if self.deleted:
            return None
        return self.target


class _StepClock:
    def __init__(self, step: float = 0.5) -> None:
        self.now = 0.0
        self._step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self._step
        return value

    async def sleep(self, _seconds: float) -> None:
        self.now += self._step


@pytest.mark.parametrize(
    ("raw", "phase"),
    [
        ("started", "requested"),
        ("stopped", "requested"),
        ("archived", "requested"),
        ("creating", "requested"),
        ("unknown", "requested"),
        ("", "requested"),
        ("destroying", "deleting"),
        ("deleting", "deleting"),
        ("archiving", "deleting"),
        ("stopping", "deleting"),
        ("destroyed", "absent"),
        ("deleted", "absent"),
        ("error", "failed"),
        ("build_failed", "failed"),
    ],
)
def test_classify_deletion_phase_maps_raw_states(raw: str, phase: str) -> None:
    from fleet_rlm.daytona.runtime import classify_deletion_phase

    assert classify_deletion_phase(raw) == phase


@pytest.mark.asyncio
async def test_delete_request_acceptance_is_not_absence() -> None:
    from fleet_rlm.daytona.runtime import AbsenceTimeout, confirm_absence

    provider = _FakeDeletionProvider(target=_FakeDeletionSandbox(state="started"))
    await provider.delete("sb-1")
    assert provider.seen_deletes == ["sb-1"]
    outcome = await confirm_absence(
        probe=provider.get,
        sandbox_id="sb-1",
        timeout_s=5.0,
        clock=_StepClock(),
        sleep=_StepClock().sleep,
    )
    assert isinstance(outcome, AbsenceTimeout)
    assert outcome.absent is False
    assert outcome.last_state == "started"


@pytest.mark.asyncio
async def test_requested_then_deleting_then_absent_and_purged() -> None:
    from fleet_rlm.daytona.runtime import AbsenceConfirmation, confirm_absence

    provider = _FakeDeletionProvider(target=_FakeDeletionSandbox(state="started"))
    clock = _StepClock()

    async def scripted_probe(sandbox_id: str) -> Any | None:
        calls = scripted_probe.calls
        scripted_probe.calls = calls + 1
        if calls == 0:
            provider.target = _FakeDeletionSandbox(state="started")
        elif calls == 1:
            provider.target = _FakeDeletionSandbox(state="destroying")
        else:
            provider.deleted = True
        return await provider.get(sandbox_id)

    scripted_probe.calls = 0

    outcome = await confirm_absence(
        probe=scripted_probe,
        sandbox_id="sb-2",
        timeout_s=30.0,
        clock=clock,
        sleep=clock.sleep,
    )
    assert isinstance(outcome, AbsenceConfirmation)
    assert outcome.absent is True
    assert outcome.observations == ("started", "destroying", "not_found")


@pytest.mark.asyncio
async def test_terminal_destroyed_state_confirms_without_purge() -> None:
    from fleet_rlm.daytona.runtime import AbsenceConfirmation, confirm_absence

    provider = _FakeDeletionProvider(target=_FakeDeletionSandbox(state="destroyed"))
    outcome = await confirm_absence(
        probe=provider.get,
        sandbox_id="sb-3",
        timeout_s=30.0,
        clock=_StepClock(),
        sleep=_StepClock().sleep,
    )
    assert isinstance(outcome, AbsenceConfirmation)
    assert outcome.observations == ("destroyed",)


@pytest.mark.asyncio
async def test_provider_error_state_is_classified_failure_not_absence() -> None:
    from fleet_rlm.daytona.runtime import AbsenceProbeError, confirm_absence

    provider = _FakeDeletionProvider(target=_FakeDeletionSandbox(state="error"))
    outcome = await confirm_absence(
        probe=provider.get,
        sandbox_id="sb-4",
        timeout_s=30.0,
        clock=_StepClock(),
        sleep=_StepClock().sleep,
    )
    assert isinstance(outcome, AbsenceProbeError)
    assert outcome.absent is False
    assert "provider error state" in outcome.error


@pytest.mark.asyncio
async def test_probe_error_is_classified_not_silent() -> None:
    from fleet_rlm.daytona.runtime import AbsenceProbeError, confirm_absence

    async def failing_probe(_sandbox_id: str) -> Any | None:
        raise RuntimeError("provider 503")

    outcome = await confirm_absence(
        probe=failing_probe,
        sandbox_id="sb-5",
        timeout_s=30.0,
        clock=_StepClock(),
        sleep=_StepClock().sleep,
    )
    assert isinstance(outcome, AbsenceProbeError)
    assert "provider 503" in outcome.error


@pytest.mark.asyncio
async def test_cancellation_propagates_instead_of_classifying() -> None:
    from fleet_rlm.daytona.runtime import confirm_absence

    async def cancelled_probe(_sandbox_id: str) -> Any | None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await confirm_absence(
            probe=cancelled_probe,
            sandbox_id="sb-6",
            timeout_s=30.0,
            clock=_StepClock(),
            sleep=_StepClock().sleep,
        )


@dataclass
class _SandboxStub:
    id: str
    state: str = "started"


class _FsStub:
    async def list_files(self, _root: str, *, depth: int | None) -> list[Any]:
        assert depth is None
        return []

    async def delete_file(self, _path: str, *, recursive: bool = False) -> None:
        del recursive
        raise AssertionError("no files should be purged in this test")


class _ScriptedPlatform:
    def __init__(self, states: list[str | None], *, delete_error: BaseException | None = None) -> None:
        self.states = list(states)
        self.delete_error = delete_error
        self.deletes: list[str] = []
        self.probes: int = 0

    async def delete(self, sandbox_id: str) -> None:
        self.deletes.append(sandbox_id)
        if self.delete_error is not None:
            raise self.delete_error

    async def get(self, sandbox_id: str) -> _SandboxStub | None:
        self.probes += 1
        if not self.states:
            return None
        state = self.states.pop(0)
        if state is None:
            return None
        return _SandboxStub(id=sandbox_id, state=state)


async def _take_permit():
    from fleet_rlm.daytona.runtime import DaytonaAdmission

    admission = DaytonaAdmission(max_active_leases=1)
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 5)
    return admission, permit


def _cleanup_coroutine(platform: _ScriptedPlatform, permit, **overrides: Any) -> Any:
    from fleet_rlm.daytona.runtime import cleanup_child_runtime_async

    kwargs: dict[str, Any] = {
        "platform": platform,
        "sandbox": _sandbox_helper(),
        "sandbox_id": "sb-ephemeral",
        "mount_path": "/mnt/data",
        "permit": permit,
        "confirm_poll_interval_s": 0.01,
        "confirm_timeout_s": 0.25,
    }
    kwargs.update(overrides)
    return cleanup_child_runtime_async(**kwargs)


def _sandbox_helper() -> Any:
    return SimpleNamespace(id="sb-ephemeral", fs=_FsStub())


@pytest.mark.asyncio
async def test_permit_released_only_after_confirmed_absent() -> None:
    platform = _ScriptedPlatform(states=["destroying", "started", None])
    _, permit = await _take_permit()
    await _cleanup_coroutine(platform, permit)
    assert platform.deletes == ["sb-ephemeral"]
    assert platform.probes == 3
    assert permit._released is True


@pytest.mark.asyncio
async def test_request_acceptance_alone_never_releases() -> None:
    platform = _ScriptedPlatform(states=["destroying", "destroying", "destroying", None])
    _, permit = await _take_permit()
    await _cleanup_coroutine(platform, permit)
    assert platform.probes == 4
    assert permit._released is True


@pytest.mark.asyncio
async def test_unconfirmed_teardown_is_explicit_quarantine_failure() -> None:
    from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError

    platform = _ScriptedPlatform(states=["destroying"] * 100)
    _, permit = await _take_permit()
    with pytest.raises(ChildRuntimeCleanupError) as excinfo:
        await _cleanup_coroutine(platform, permit)
    assert "absence unconfirmed" in str(excinfo.value)
    assert permit._released is False
    permit.release()


@pytest.mark.asyncio
async def test_delete_request_error_still_probes_and_surfaces_error() -> None:
    platform = _ScriptedPlatform(states=[None], delete_error=RuntimeError("provider 503"))
    _, permit = await _take_permit()
    with pytest.raises(RuntimeError, match="provider 503"):
        await _cleanup_coroutine(platform, permit)
    assert platform.probes == 1
    assert permit._released is True


@pytest.mark.asyncio
async def test_provider_error_state_is_quarantine_failure() -> None:
    from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError

    platform = _ScriptedPlatform(states=["error"])
    _, permit = await _take_permit()
    with pytest.raises(ChildRuntimeCleanupError):
        await _cleanup_coroutine(platform, permit)
    assert permit._released is False
    permit.release()


@pytest.mark.asyncio
async def test_already_absent_sandbox_releases_promptly() -> None:
    platform = _ScriptedPlatform(states=[None])
    _, permit = await _take_permit()
    await _cleanup_coroutine(platform, permit)
    assert platform.probes == 1
    assert permit._released is True


@pytest.mark.asyncio
async def test_double_release_is_idempotent() -> None:
    platform = _ScriptedPlatform(states=[None])
    admission, permit = await _take_permit()
    await _cleanup_coroutine(platform, permit)
    permit.release()
    permit.release()
    permit2 = await admission.acquire(deadline=asyncio.get_running_loop().time() + 5)
    permit2.release()


@pytest.mark.asyncio
async def test_confirmation_timeout_retains_permit_for_recovery() -> None:
    from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError

    platform = _ScriptedPlatform(states=["destroying"] * 1000)
    _, permit = await _take_permit()
    with pytest.raises(ChildRuntimeCleanupError):
        await _cleanup_coroutine(platform, permit, confirm_timeout_s=0.05)
    assert permit._released is False
    permit.release()


@pytest.mark.parametrize(
    ("error_type", "category"),
    [(DaytonaQueueTimeoutError, "timeout"), (DaytonaSpotEvictedError, "unknown")],
)
def test_destroyed_sandbox_errors_preserve_sanitized_provider_failure(error_type, category) -> None:
    raw = error_type("sandbox destroyed api_key=private")
    mapped = map_provider_error(raw)
    assert isinstance(mapped, ProviderRequestError)
    assert mapped.cause_type == error_type.__name__
    assert "private" not in str(mapped)
    for error in (raw, mapped):
        assert classify_provider_error(error) == category
        assert not is_sandbox_not_found(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [DaytonaQueueTimeoutError, DaytonaSpotEvictedError])
@pytest.mark.parametrize("operation", ["create", "start"])
async def test_native_destroyed_error_does_not_retry_provider_operation(error_type, operation) -> None:
    client = SimpleNamespace(
        create=AsyncMock(side_effect=error_type("sandbox destroyed")),
        get=AsyncMock(return_value=SimpleNamespace(id="sandbox")),
        start=AsyncMock(side_effect=error_type("sandbox destroyed")),
    )
    platform = LiveDaytonaPlatform(client, _SPEC)
    with pytest.raises(ProviderRequestError) as raised:
        if operation == "create":
            await platform.create(with_volume=False)
        else:
            await platform.start("sandbox")
    assert raised.value.cause_type == error_type.__name__
    getattr(client, operation).assert_awaited_once()
    if operation == "create":
        assert client.create.await_args.args[0].queue_timeout is None
