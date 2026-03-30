from __future__ import annotations

from fleet_rlm.api import bootstrap_observability


class _CaptureClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def capture(
        self,
        event: str,
        *,
        distinct_id: str,
        properties: dict[str, object],
    ) -> None:
        self.calls.append((event, distinct_id, properties))


def test_emit_posthog_startup_event_returns_false_without_client(monkeypatch):
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.client.get_posthog_client",
        lambda _: None,
    )

    emitted = bootstrap_observability.emit_posthog_startup_event(
        app_env="local",
        auth_mode="dev",
        database_required=False,
    )

    assert emitted is False


def test_emit_posthog_startup_event_captures_event(monkeypatch):
    client = _CaptureClient()
    monkeypatch.setenv("POSTHOG_DISTINCT_ID", "runtime-user")
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.client.get_posthog_client",
        lambda _: client,
    )

    emitted = bootstrap_observability.emit_posthog_startup_event(
        app_env="staging",
        auth_mode="dev",
        database_required=True,
    )

    assert emitted is True
    assert len(client.calls) == 1
    event, distinct_id, properties = client.calls[0]
    assert event == "posthog_analytics_initialized"
    assert distinct_id == "runtime-user"
    assert properties["component"] == "server"
    assert properties["app_env"] == "staging"
    assert properties["database_required"] is True


def test_emit_posthog_startup_event_handles_capture_error(monkeypatch):
    class _FailingClient:
        def capture(self, *_args, **_kwargs) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.client.get_posthog_client",
        lambda _: _FailingClient(),
    )

    emitted = bootstrap_observability.emit_posthog_startup_event(
        app_env="local",
        auth_mode="dev",
        database_required=False,
    )

    assert emitted is False
