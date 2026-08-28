"""Focused contracts for reusable Session-scoped Tool proxies."""

from __future__ import annotations

from uuid import uuid4

import dspy
import pytest

from fleet_rlm.rlm.session_runtime import (
    SessionToolAuthorizationError,
    SessionToolRegistry,
)


def _tool(name: str, prefix: str, *, desc: str = "Safe host capability") -> dspy.Tool:
    def invoke(value: str) -> str:
        return f"{prefix}:{value}"

    return dspy.Tool(invoke, name=name, desc=desc)


def test_proxy_identity_is_stable_and_calls_resolve_current_binding() -> None:
    registry = SessionToolRegistry()
    first = registry.install(
        (_tool("echo", "first"),),
        run_id="run-1",
        claim_valid=lambda: True,
        authorized_names={"echo"},
    )[0]

    assert isinstance(first, dspy.Tool)
    assert first.func(value="one") == "first:one"

    second = registry.install(
        (_tool("echo", "second"),),
        run_id="run-2",
        claim_valid=lambda: True,
        authorized_names={"echo"},
    )[0]

    assert second is first
    assert first.func(value="two") == "second:two"


def test_stale_cleared_and_expired_bindings_fail_closed() -> None:
    live = True
    registry = SessionToolRegistry()
    lease = registry.bind_turn(
        (_tool("echo", "live"),),
        run_id="run-1",
        claim_valid=lambda: live,
        authorized_names={"echo"},
    )
    proxy = lease.tools[0]
    assert proxy.func(value="ok") == "live:ok"

    live = False
    with pytest.raises(SessionToolAuthorizationError):
        proxy.func(value="expired")

    live = True
    assert lease.remove()
    with pytest.raises(SessionToolAuthorizationError):
        proxy.func(value="cleared")
    assert registry.tools() == ()


@pytest.mark.asyncio
async def test_retiring_binding_cancels_suspended_async_tool_call() -> None:
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()
    side_effects: list[str] = []

    async def invoke(value: str) -> str:
        started.set()
        await release.wait()
        side_effects.append(value)
        return value

    registry = SessionToolRegistry()
    source = dspy.Tool(invoke, name="echo", desc="Echo", args={"value": {"type": "string"}})
    lease = registry.bind_turn(
        (source,),
        run_id="run-1",
        claim_valid=lambda: True,
        authorized_names={"echo"},
    )
    task = asyncio.create_task(lease.tools[0].func(value="stale"))
    await started.wait()

    assert lease.remove()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert side_effects == []


def test_stale_lease_cannot_remove_a_new_run_binding() -> None:
    registry = SessionToolRegistry()
    old = registry.bind_turn(
        (_tool("echo", "old"),),
        run_id="run-1",
        claim_valid=lambda: True,
        authorized_names={"echo"},
    )
    current = registry.bind_turn(
        (_tool("echo", "current"),),
        run_id="run-2",
        claim_valid=lambda: True,
        authorized_names={"echo"},
    )

    assert old.remove() is False
    assert current.tools[0].func(value="value") == "current:value"


def test_unauthorized_names_and_missing_authorization_fail_closed() -> None:
    registry = SessionToolRegistry()
    proxies = {
        str(tool.name): tool
        for tool in registry.install(
            (_tool("allowed", "yes"), _tool("blocked", "no")),
            run_id="run-1",
            claim_valid=lambda: True,
            authorized_names={"allowed"},
        )
    }

    assert proxies["allowed"].func(value="ok") == "yes:ok"
    with pytest.raises(SessionToolAuthorizationError):
        proxies["blocked"].func(value="must-not-run")

    no_auth = SessionToolRegistry()
    inert = no_auth.install(
        (_tool("echo", "no-auth"),),
        run_id="run-2",
        claim_valid=lambda: True,
    )[0]
    with pytest.raises(SessionToolAuthorizationError):
        inert.func(value="must-not-run")


def test_removed_invocation_only_alias_is_not_in_the_next_program_set() -> None:
    registry = SessionToolRegistry()
    first = registry.install(
        (_tool("stable", "stable"), _tool("invoke_once", "old")),
        run_id="run-1",
        claim_valid=lambda: True,
        authorized_names={"stable", "invoke_once"},
    )
    old_alias = first[1]

    current = registry.install(
        (_tool("stable", "new"),),
        run_id="run-2",
        claim_valid=lambda: True,
        authorized_names={"stable"},
    )

    assert registry.active_names == {"stable"}
    assert registry.tools() == current
    assert old_alias not in registry.tools()
    with pytest.raises(SessionToolAuthorizationError):
        old_alias.func(value="removed")
    assert current[0].func(value="current") == "new:current"


def test_proxy_preserves_exact_source_metadata_without_retaining_source_tool() -> None:
    secret = "run-secret-payload-DO-NOT-RETAIN"

    def read(value: str) -> str:
        return value

    source = dspy.Tool(
        read,
        name="read_safe",
        desc=("Safe description " * 100) + f" token={secret}",
        args={
            "value": {
                "type": "string",
                "description": secret,
                "default": secret,
            },
        },
        arg_types={"value": str},
        arg_desc={"value": secret},
    )
    registry = SessionToolRegistry()
    proxy = registry.install(
        (source,),
        run_id="run-1",
        claim_valid=lambda: True,
        authorized_names={"read_safe"},
    )[0]

    assert proxy.name == source.name
    assert proxy.desc == source.desc
    assert proxy.args == source.args
    assert proxy.arg_types == source.arg_types
    assert proxy.arg_desc == source.arg_desc
    assert proxy.args is not source.args
    assert proxy.arg_desc is not source.arg_desc
    assert proxy.func(value="public") == "public"


@pytest.mark.asyncio
async def test_runner_caches_one_tool_registry_per_session_key() -> None:
    """The runner's inline tenancy cache keys registries by full Session identity."""
    from fleet_rlm.rlm.runtime import RLMRunner
    from fleet_rlm.rlm.session_runtime import SessionKey

    runner = RLMRunner()
    try:
        cache = runner._session_tool_registries
        assert cache == {}
        first = SessionKey(workspace_id=uuid4(), session_id=uuid4())
        second = SessionKey(workspace_id=first.workspace_id, session_id=uuid4())
        registry_a, registry_b = SessionToolRegistry(), SessionToolRegistry()
        cache[first] = registry_a
        cache[second] = registry_b
        assert cache[first] is registry_a
        assert cache[second] is registry_b
        assert cache[first] is not cache[second]
    finally:
        await runner.aclose()
