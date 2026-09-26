"""Public Daytona runtime facade and client-construction contracts.

* ``test_runtime.py``: Focused lifecycle contracts for the public Daytona runtime facade.
* ``test_daytona_platform.py``: Daytona 0.210.0 client construction contracts.
"""

from __future__ import annotations

import warnings
from importlib.metadata import version
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.runtime import ChildEnvironmentSpec, DaytonaRuntime, RootSessionSpec, build_daytona_client


# --- from test_runtime.py ---------------------------------------------
@pytest.mark.asyncio
async def test_runtime_close_retains_a_failed_root_for_retry() -> None:
    calls = 0

    async def release(_lease: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider close failed")

    runtime = DaytonaRuntime(
        root_acquirer=lambda _spec, **_kwargs: object(),
        root_releaser=release,
    )
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())
    await runtime.acquire_root_session(spec)

    assert await runtime.aclose() is False
    assert len(runtime.roots) == 1
    assert runtime.state.value == "FAILED"

    assert await runtime.aclose() is True
    assert runtime.roots == ()
    assert calls == 2


@pytest.mark.asyncio
async def test_successful_child_close_deregisters_from_runtime() -> None:
    class Lease:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    lease = Lease()
    runtime = DaytonaRuntime(child_acquirer=lambda _spec: lease)
    spec = ChildEnvironmentSpec()

    async with runtime.open_child(spec):
        assert len(runtime.children) == 1

    assert runtime.children == ()
    assert lease.close_calls == 1


@pytest.mark.asyncio
async def test_child_runtime_uses_configured_rlm_execution_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def build_factory(**kwargs: object) -> object:
        captured.update(kwargs)
        return lambda _call_index: "child-lease"

    monkeypatch.setattr(recursive_child_runtime, "build_child_runtime_factory", build_factory)
    settings = SimpleNamespace(rlm_execution_timeout_s=37, rlm_max_execution_output_chars=1234)
    resources = SimpleNamespace(
        platform=object(),
        daytona_admission=object(),
        settings=settings,
        dispatcher=None,
    )
    runtime = DaytonaRuntime(resources)
    spec = ChildEnvironmentSpec(
        workspace_id=uuid4(),
        run_id=uuid4(),
        volume_id="volume",
        mount_path="/home/daytona/fleet",
        call_index=4,
    )

    assert await runtime._acquire_child_from_resources(spec) == "child-lease"
    assert captured["execution_timeout_s"] == 37
    assert captured["execution_output_cap"] == 1234


# --- from test_daytona_platform.py ------------------------------------
@pytest.mark.asyncio
async def test_build_daytona_client_uses_explicit_api_url_without_deprecation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAYTONA_API_URL", "https://ambient.example/api")
    settings = Settings(
        daytona_api_key=SecretStr("test-daytona-key"),
        daytona_org_id="test-org",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client = build_daytona_client(settings)

    try:
        assert version("daytona") == "0.210.0"
        assert client._api_url == "https://app.daytona.io/api"
        assert client._api_client.default_headers["X-Daytona-Organization-ID"] == "test-org"
        assert not any("server_url" in str(item.message) for item in caught)
    finally:
        await client.close()
