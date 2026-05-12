from datetime import datetime, timezone

from fleet_rlm.api.events.event_adapter import adapt_stream_event, build_chat_event_payload


def test_adapt_stream_event_promotes_runtime_context() -> None:
    event = adapt_stream_event(
        kind="status",
        text="Preparing Daytona workspace...",
        payload={
            "phase": "startup",
            "runtime": {
                "runtime_mode": "daytona_pilot",
                "execution_mode": "auto",
                "sandbox_id": "sbx-123",
                "volume_name": "workspace-a",
                "workspace_path": "/workspace",
                "depth": 1,
                "max_depth": 3,
            },
        },
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert event.kind == "status"
    assert event.runtime is not None
    assert event.runtime.runtime_mode == "daytona_pilot"
    assert event.runtime.sandbox_id == "sbx-123"
    assert event.runtime.volume_name == "workspace-a"
    assert event.runtime.depth == 1
    assert event.runtime.max_depth == 3


def test_build_chat_event_payload_includes_runtime_projection() -> None:
    event = adapt_stream_event(
        kind="done",
        text="Final answer",
        payload={
            "trajectory": {"steps": []},
            "runtime": {
                "runtime_mode": "daytona_pilot",
                "execution_profile": "rlm_delegate",
                "sandbox_id": "sbx-999",
            },
        },
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    payload = build_chat_event_payload(event)

    assert payload["kind"] == "done"
    assert payload["text"] == "Final answer"
    assert payload["version"] == 3
    assert payload["payload"]["runtime"]["runtime_mode"] == "daytona_pilot"
    assert payload["payload"]["runtime"]["execution_profile"] == "rlm_delegate"
    assert payload["payload"]["runtime"]["sandbox_id"] == "sbx-999"