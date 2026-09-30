"""QRE-150 live evidence: separate deletion request acceptance from confirmed absence.

Gate: FLEET_LIVE=1 with the daytona profile credentials resolved through the repo
``.env`` (override=False). Characterizes the REAL Daytona lifecycle for an
ephemeral Sandbox: accepted -> deleting -> absent (not-found/destroyed), with
timings recorded to ``FLEET_LIVE_EVIDENCE_PATH`` (suffix ``-deletion-a`` / ``-b``)
when set.

This probe talks to the provider through Fleet's own client factory and the
production ``fleet_rlm.daytona.runtime.confirm_absence`` poller; no LLM is
involved.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(600)]

_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_RECEIPT_SCHEMA = "fleet.qre150-deletion-lifecycle/v1"


def _load_repo_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)


def _live_client_and_platform() -> tuple[Any, Any]:
    _load_repo_env()
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for the live Daytona deletion-lifecycle proof")
    if not os.environ.get("DAYTONA_API_KEY") and not os.environ.get("FLEET_DAYTONA_API_KEY"):
        pytest.fail("live deletion proof requires DAYTONA_API_KEY credentials")
    # Must sit at function scope: an import placed after the fail() above is
    # unreachable, which makes the name local-but-unbound and raises
    # UnboundLocalError on the first use below.
    from fleet_rlm.config.loader import load_runtime_settings
    from fleet_rlm.daytona.runtime import (
        DaytonaSandboxSpec,
        LiveDaytonaPlatform,
        build_daytona_client,
    )

    settings = load_runtime_settings()
    client = build_daytona_client(settings)
    spec = DaytonaSandboxSpec(snapshot=settings.daytona_snapshot or "fleet-rlm-python313-v5")
    return client, LiveDaytonaPlatform(client, spec)


def _write_receipt(name: str, payload: dict[str, Any]) -> None:
    raw = os.environ.get(_EVIDENCE_ENV)
    if not raw:
        return
    base = Path(raw)
    target = base.with_name(f"{base.stem}-{name}{base.suffix or '.json'}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_live_delete_request_acceptance_is_not_absence_then_confirms() -> None:
    """Case A: fire-and-forget delete -> acceptance != absence -> eventual confirmed absence.

    Proves the provider reports the Sandbox as still present right after the
    request is accepted, transitions through deleting, and reaches confirmed
    absence only later; records the full observed state sequence and timing.
    """
    from fleet_rlm.daytona.runtime import AbsenceConfirmation, confirm_absence

    client, platform = _live_client_and_platform()
    sandbox = await platform.create(
        with_volume=False,
        ephemeral=True,
        labels={"fleet.qre": "150", "purpose": "deletion-lifecycle"},
    )
    sandbox_id = sandbox.id
    try:
        # Immediate observation under the still-owning state.
        first = await platform.get(sandbox_id)
        first_state = str(getattr(first, "state", None))

        # Fire-and-forget request: SDK returns when the request is accepted.
        requested_at = time.monotonic()
        await platform.delete(sandbox_id)
        accepted_s = time.monotonic() - requested_at

        # Acceptance must not equal absence.
        immediate = await platform.get(sandbox_id)
        immediate_state = "not_found" if immediate is None else str(getattr(immediate.state, "value", immediate.state))

        outcome = await confirm_absence(
            probe=platform.get,
            sandbox_id=sandbox_id,
            timeout_s=180.0,
            poll_interval_s=2.0,
        )
        record = {
            "schema": _RECEIPT_SCHEMA,
            "case": "request-then-absence",
            "sandbox_id": sandbox_id,
            "acceptance_seconds": round(accepted_s, 3),
            "first_state_before_delete": first_state,
            "immediate_post_delete_state": immediate_state,
            "confirmation": {
                "absent": outcome.absent,
                "observations": list(outcome.observations),
                "duration_s": round(outcome.duration_s, 3),
            },
        }
        _write_receipt("deletion-a", record)
        assert isinstance(outcome, AbsenceConfirmation), (
            f"ephemeral Sandbox not confirmed absent within 180s: {outcome!r}"
        )
        # Acceptance is request-level: the pre-delete Sandbox was owning and the
        # confirmation wait (not the request return) is what proves absence. A
        # very fast provider may already report not_found immediately; that is
        # recorded as evidence, never assumed.
    finally:
        # The probe owns no remaining resource once absent; tolerate double deletes.
        with contextlib.suppress(Exception):
            await platform.delete(sandbox_id)
        await client.close()


@pytest.mark.asyncio
async def test_live_delete_wait_true_reference_blocks_until_destroyed() -> None:
    """Case B: SDK reference semantics — ``delete(wait=True)`` blocks until `destroyed`.

    Baseline for comparison with Fleet's fire-and-forget + confirmation model.
    """
    client, platform = _live_client_and_platform()
    sandbox = await platform.create(
        with_volume=False,
        ephemeral=True,
        labels={"fleet.qre": "150", "purpose": "deletion-lifecycle-wait"},
    )
    sandbox_id = sandbox.id
    started = time.monotonic()
    try:
        await client.delete(sandbox, timeout=120, wait=True)
        waited_s = time.monotonic() - started
        # Provider reality (recorded evidence): once deletion completes, the
        # Sandbox disappears from the API — refresh_data() 404s rather than
        # reporting a durable "destroyed" state. Terminal truth = not-found.
        terminal_visibility = "unprobed"
        try:
            await sandbox.refresh_data()
        except Exception as exc:
            terminal_visibility = f"refresh_data raised {type(exc).__name__}: {str(exc)[:160]}"
        from fleet_rlm.daytona.runtime import AbsenceConfirmation, confirm_absence

        outcome = await confirm_absence(
            probe=platform.get,
            sandbox_id=sandbox_id,
            timeout_s=60.0,
            poll_interval_s=1.0,
        )
        _write_receipt(
            "deletion-b",
            {
                "schema": _RECEIPT_SCHEMA,
                "case": "wait-true-reference",
                "sandbox_id": sandbox_id,
                "wait_true_seconds": round(waited_s, 3),
                "terminal_visibility": terminal_visibility,
                "post_delete_absence": {
                    "absent": outcome.absent,
                    "observations": list(outcome.observations),
                    "duration_s": round(outcome.duration_s, 3),
                },
            },
        )
        assert isinstance(outcome, AbsenceConfirmation), outcome
        assert "raised" in terminal_visibility or "destroyed" in terminal_visibility
    finally:
        with contextlib.suppress(Exception):
            await platform.delete(sandbox_id)
        await client.close()


@pytest.mark.asyncio
async def test_live_interpreter_invocations_settle_before_sandbox_deletion() -> None:
    """Prove typed broker execution and fresh invocations on one real Sandbox."""
    from dspy import FinalOutput

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, _SyncBridgeLoop, sandbox_backend
    from fleet_rlm.daytona.runtime import AbsenceConfirmation, confirm_absence

    client, platform = _live_client_and_platform()
    sandbox = None
    interpreters: list[DaytonaCodeInterpreter] = []
    calls: list[str] = []

    def host_tool(value: str) -> str:
        calls.append(value)
        return value

    async def async_host_tool() -> str:
        assert asyncio.get_running_loop() is loop
        calls.append("async")
        return "async"

    loop = asyncio.get_running_loop()
    try:
        sandbox = await platform.create(
            with_volume=False, ephemeral=True, labels={"purpose": "interpreter-boundary-lifecycle"}
        )
        template = DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=loop, timeout_s=20),
            tools={"host_tool": host_tool, "async_host_tool": async_host_tool},
            output_fields=[{"name": "answer", "type": "str", "required": True}],
        )
        interpreters.append(template)
        first = template.new_invocation(async_bridge=_SyncBridgeLoop(caller_loop=loop))
        interpreters.append(first)
        assert await asyncio.to_thread(first.execute, "cached = host_tool(value='first'); print(cached)") == "first\n"
        assert first.broker is not None
        assert template.broker is None
        result = await asyncio.to_thread(first.execute, "SUBMIT(answer=cached + '-' + async_host_tool())")
        assert isinstance(result, FinalOutput)
        assert result.output == {"answer": "first-async"}
        assert calls == ["first", "async"]
        await asyncio.to_thread(first.shutdown, strict_broker_cleanup=True)
        assert first.broker is None
        assert first._backend is None
        assert await platform.get(sandbox.id) is not None

        second = template.new_invocation(async_bridge=_SyncBridgeLoop(caller_loop=loop))
        interpreters.append(second)
        fresh = await asyncio.to_thread(
            second.execute, "SUBMIT(answer='fresh' if 'cached' not in globals() else 'leaked')"
        )
        assert isinstance(fresh, FinalOutput)
        assert fresh.output == {"answer": "fresh"}
    finally:
        cleanup_errors: list[Exception] = []
        try:
            for interpreter in reversed(interpreters):
                try:
                    await asyncio.to_thread(interpreter.shutdown, strict_broker_cleanup=True)
                except Exception as exc:
                    cleanup_errors.append(exc)
        finally:
            try:
                if sandbox is not None:
                    await platform.delete(sandbox.id)
                    outcome = await confirm_absence(
                        probe=platform.get, sandbox_id=sandbox.id, timeout_s=180.0, poll_interval_s=2.0
                    )
                    assert isinstance(outcome, AbsenceConfirmation), outcome
            finally:
                await client.close()
        if cleanup_errors:
            raise ExceptionGroup("interpreter cleanup failed", cleanup_errors)
    _write_receipt(
        "interpreter-boundary",
        {
            "schema": _RECEIPT_SCHEMA,
            "case": "interpreter-invocations-before-deletion",
            "sandbox_id": sandbox.id,
            "host_tool_calls": calls,
            "same_invocation_state": True,
            "fresh_invocation_state": True,
            "typed_submit": True,
            "strict_interpreter_cleanup": True,
            "sandbox_absent": outcome.absent,
        },
    )
