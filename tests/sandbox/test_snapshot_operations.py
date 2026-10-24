"""Operator actions preserve immutable snapshots and do not replay ambiguous mutations."""

from types import SimpleNamespace

import pytest
from daytona import DaytonaNotFoundError

from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME, SnapshotUnavailableError, operations


def test_existing_snapshot_is_never_replaced(monkeypatch):
    service = SimpleNamespace(
        get=lambda _name: SimpleNamespace(id="existing"),
        create=lambda *_a, **_kw: pytest.fail("must not replace existing snapshot"),
    )
    monkeypatch.setattr(operations, "Daytona", lambda _config: SimpleNamespace(snapshot=service))
    with pytest.raises(SnapshotUnavailableError, match="already exists"):
        operations.build_snapshot(api_key="key", org_id="org")


def test_lookup_failure_does_not_turn_into_snapshot_creation(monkeypatch):
    def unavailable(_name):
        raise TimeoutError("private-provider-detail")

    service = SimpleNamespace(get=unavailable, create=lambda *_a, **_kw: pytest.fail("unknown lookup is not missing"))
    monkeypatch.setattr(operations, "Daytona", lambda _config: SimpleNamespace(snapshot=service))
    with pytest.raises(SnapshotUnavailableError) as error:
        operations.build_snapshot(api_key="key", org_id="org")
    assert "private-provider-detail" not in str(error.value)
    assert error.value.state == "lookup_failed"


def test_activation_runs_once_then_verifies_provider_identity(monkeypatch):
    state = ["inactive"]
    calls = []

    def activate(name):
        calls.append(name)
        state[0] = "active"

    service = SimpleNamespace(
        get=lambda name: SimpleNamespace(name=name, id="provider-id", state=state[0], cpu=4, mem=8, disk=8),
        activate=activate,
    )
    monkeypatch.setattr(operations, "Daytona", lambda _config: SimpleNamespace(snapshot=service))
    identity = operations.activate_snapshot(api_key="key", org_id="org")
    assert calls == [SNAPSHOT_NAME]
    assert identity.id == "provider-id"
    # A second explicit activation only verifies the already active snapshot.
    assert operations.activate_snapshot(api_key="key", org_id="org").id == "provider-id"
    assert calls == [SNAPSHOT_NAME]


def test_activation_does_not_create_a_missing_snapshot(monkeypatch):
    def missing(_name):
        raise DaytonaNotFoundError("missing")

    service = SimpleNamespace(get=missing, activate=lambda *_a: pytest.fail("missing snapshot cannot be activated"))
    monkeypatch.setattr(operations, "Daytona", lambda _config: SimpleNamespace(snapshot=service))
    with pytest.raises(SnapshotUnavailableError) as error:
        operations.activate_snapshot(api_key="key", org_id="org")
    assert error.value.state == "missing"
