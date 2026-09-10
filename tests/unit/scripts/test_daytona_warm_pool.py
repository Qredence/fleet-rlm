from __future__ import annotations

import argparse

import pytest

from fleet_rlm.daytona.warm_pool import WarmPoolError, WarmPoolPlan
from scripts import daytona_warm_pool
from scripts.daytona_warm_pool import _parser, _require_operator_preflight


def _plan(target: str | None) -> WarmPoolPlan:
    return WarmPoolPlan("snapshot", target, 1, True, "a" * 64)


def _args(*values: str) -> argparse.Namespace:
    return _parser().parse_args(("check", *values))


def test_enabled_provider_operations_require_complete_campaign_and_named_target() -> None:
    with pytest.raises(WarmPoolError, match="complete campaign"):
        _require_operator_preflight(_args(), _plan("us"))
    complete = _args(
        "--campaign",
        "campaign",
        "--spend-cap",
        "10",
        "--elapsed-seconds",
        "60",
        "--admission-limit",
        "1",
        "--sandbox-concurrency",
        "1",
    )
    assert _require_operator_preflight(complete, _plan("us")).name == "campaign"
    with pytest.raises(WarmPoolError, match="explicit warm-pool region"):
        _require_operator_preflight(complete, _plan(None))


def test_check_parser_requires_the_same_campaign_contract_as_reconcile() -> None:
    with pytest.raises(WarmPoolError, match="complete campaign"):
        _require_operator_preflight(_args(), _plan("us"))


@pytest.mark.asyncio
async def test_disabled_check_returns_without_live_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = object()
    disabled = WarmPoolPlan("snapshot", None, 0, False, "a" * 64)
    monkeypatch.setattr(daytona_warm_pool, "load_runtime_settings", lambda: settings)
    monkeypatch.setattr(daytona_warm_pool.WarmPoolPlan, "from_settings", classmethod(lambda _cls, _settings: disabled))

    def unexpected_live_gate() -> object:
        raise AssertionError("disabled warm-pool checks must not require live credentials")

    monkeypatch.setattr(daytona_warm_pool, "require_live_execution", unexpected_live_gate)
    result = await daytona_warm_pool._run(_parser().parse_args(("check",)))

    assert result["action"] == "disabled"
    assert result["desired_size"] == 0
