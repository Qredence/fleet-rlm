"""Characterization tests for current API, websocket, and CLI public contracts."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from fleet_rlm.api.events.event_adapter import adapt_stream_event, build_chat_event_payload
from fleet_rlm.api.schemas.websocket import WSMessage
from fleet_rlm.cli.fleet_cli import app as fleet_rlm_app

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    cleaned = _ANSI_RE.sub("", text)
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        cleaned = cleaned.replace(dash, "-")
    return cleaned


def test_ws_message_accepts_current_daytona_start_payload() -> None:
    """Canonical websocket start payload keeps Daytona controls and no runtime-mode switch."""
    message = WSMessage.model_validate(
        {
            "type": "message",
            "content": "Inspect this repository",
            "trace_mode": "compact",
            "execution_mode": "auto",
            "repo_url": "https://github.com/Qredence/fleet-rlm.git",
            "repo_ref": "main",
            "context_paths": ["src/fleet_rlm"],
            "batch_concurrency": 2,
            "session_id": "session-123",
        }
    )

    assert message.type == "message"
    assert message.execution_mode == "auto"
    assert message.repo_ref == "main"
    assert message.context_paths == ["src/fleet_rlm"]
    assert not hasattr(message, "runtime_mode")


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            {"type": "message", "content": "hello", "max_depth": 3},
            "daytona_max_depth_removed",
        ),
        (
            {"type": "message", "content": "hello", "workspace_id": "forged"},
            "unsupported_identity_fields",
        ),
        (
            {"type": "message", "content": "hello", "user_id": "forged"},
            "unsupported_identity_fields",
        ),
        (
            {"type": "message", "content": "hello", "repo_ref": "main"},
            "daytona_repo_ref_requires_repo",
        ),
    ],
)
def test_ws_message_rejects_deleted_or_forged_start_payload_fields(
    payload: dict[str, object],
    expected_code: str,
) -> None:
    """Legacy request-side depth and client-supplied identity are rejected at schema boundary."""
    with pytest.raises(ValidationError) as exc_info:
        WSMessage.model_validate(payload)

    assert exc_info.value.errors()[0]["type"] == expected_code


def test_event_adapter_projects_terminal_and_delegate_events_to_current_chat_shape() -> None:
    """Current stream events use done/error chat kinds and typed rlm_delegate status projection."""
    completed = adapt_stream_event(
        kind="done",
        text="final answer",
        payload={"answer": "final answer"},
        timestamp=None,
    )
    failed = adapt_stream_event(
        kind="error",
        text="safe error",
        payload={"reason": "budget_exhausted"},
        timestamp=None,
    )
    delegated = adapt_stream_event(
        kind="status",
        text="delegating",
        payload={"phase": "delegate", "child_sandbox_id": "child-1", "depth": 1, "llm_call_budget": 4},
        timestamp=None,
    )

    assert build_chat_event_payload(completed)["kind"] == "done"
    assert build_chat_event_payload(failed)["kind"] == "error"
    delegated_payload = build_chat_event_payload(delegated)
    assert delegated_payload["kind"] == "rlm_delegate"
    assert delegated_payload["payload"]["runtime"]["child_sandbox_id"] == "child-1"
    assert delegated_payload["payload"]["runtime"]["depth"] == 1
    assert delegated_payload["payload"]["runtime"]["llm_call_budget"] == 4


@pytest.mark.parametrize("legacy_kind", ["token", "final", "plan", "hitl_request", "HITL_REQUIRED"])
def test_event_adapter_does_not_normalize_deleted_legacy_event_kinds(legacy_kind: str) -> None:
    """Deleted event aliases are not translated into canonical terminal/tool/clarification events."""
    event = adapt_stream_event(
        kind=legacy_kind,
        text="legacy payload",
        payload={"kind": legacy_kind},
        timestamp=None,
    )

    assert event.kind == "text"
    assert build_chat_event_payload(event)["kind"] == "text"


def test_fleet_rlm_help_characterizes_current_command_inventory() -> None:
    """Published fleet-rlm help exposes current canonical commands and no removed provider aliases."""
    result = CliRunner().invoke(fleet_rlm_app, ["--help"])

    assert result.exit_code == 0
    help_text = _plain(result.output)
    for command in ("serve-api", "chat", "daytona-smoke", "daytona-snapshot", "optimize"):
        assert command in help_text
    for deleted_alias in ("modal-smoke", "serve-ui", "websocket-server"):
        assert deleted_alias not in help_text
