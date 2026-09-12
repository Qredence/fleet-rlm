"""Persistence observations retain outcomes without capturing private SQL data."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm.observability import tracing
from fleet_rlm.persistence.database import observe_database_operation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, outcome",
    [
        (None, "completed"),
        (RuntimeError, "failed"),
        (SQLAlchemyError, "database_error"),
        (TimeoutError, "timeout"),
        (asyncio.CancelledError, "cancelled"),
    ],
)
async def test_observations_preserve_results_and_only_emit_bounded_metadata(monkeypatch, caplog, failure, outcome):
    observations = []
    secret = "private-sql-parameter-sentinel"

    def start(name, *, inputs):
        observations.append((name, inputs))
        return SimpleNamespace(finish=lambda **kwargs: observations.append(kwargs))

    monkeypatch.setattr(tracing, "start_turn_span", start)
    raised = failure(secret) if failure else None
    returned = object()

    @observe_database_operation("claim")
    async def operation(_private_value):
        if raised is not None:
            raise raised
        return returned

    with caplog.at_level(logging.INFO, logger="fleet_rlm.persistence.database"):
        if raised is None:
            assert await operation(secret) is returned
        else:
            with pytest.raises(type(raised)) as caught:
                await operation(secret)
            assert caught.value is raised
    assert observations[0] == ("database.claim", {"operation": "claim"})
    assert observations[1]["outputs"]["outcome"] == outcome
    assert observations[1]["outputs"]["duration_ms"] >= 0
    assert secret not in repr(observations) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["start", "finish", "log"])
@pytest.mark.parametrize("fails", [False, True])
async def test_observation_failures_do_not_change_repository_outcome(monkeypatch, broken, fails):
    from fleet_rlm.persistence import database

    def explode(*_args, **_kwargs):
        raise RuntimeError("observation unavailable")

    monkeypatch.setattr(
        tracing,
        "start_turn_span",
        explode
        if broken == "start"
        else lambda *_args, **_kwargs: SimpleNamespace(
            finish=explode if broken == "finish" else lambda **_kwargs: None
        ),
    )
    if broken == "log":
        monkeypatch.setattr(database.logger, "info", explode)
    failure = ValueError("repository failure")

    @observe_database_operation("commit")
    async def operation():
        if fails:
            raise failure
        return 42

    if fails:
        with pytest.raises(ValueError) as caught:
            await operation()
        assert caught.value is failure
    else:
        assert await operation() == 42
