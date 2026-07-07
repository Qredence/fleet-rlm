"""Tests for ChatRequest and ChatMessage pydantic schemas."""

from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError


def _chat() -> type:
    return importlib.import_module("fleet_rlm.api.schemas.chat")


def test_chat_request_model_fields():
    """ChatRequest.model_fields contains messages + all 10 fleet control fields."""
    chat = _chat()
    fields = chat.ChatRequest.model_fields

    # messages is required, not optional
    assert "messages" in fields
    assert fields["messages"].is_required()

    # All 10 control fields present and optional
    control_fields = [
        "session_id",
        "execution_mode",
        "repo_url",
        "repo_ref",
        "context_paths",
        "batch_concurrency",
        "docs_path",
        "trace",
        "trace_mode",
        "selected_skill_ids",
    ]
    for cf in control_fields:
        assert cf in fields, f"Missing control field: {cf}"
        assert not fields[cf].is_required(), f"Control field {cf} should be optional"


def test_chat_message_model_fields():
    """ChatMessage.model_fields contains role/content/parts with role Literal."""
    chat = _chat()
    fields = chat.ChatMessage.model_fields

    assert "role" in fields
    assert "content" in fields
    assert "parts" in fields

    # role is required
    assert fields["role"].is_required()

    # content and parts are optional
    assert not fields["content"].is_required()
    assert not fields["parts"].is_required()


def test_chat_message_accepts_valid_roles():
    """ChatMessage accepts all valid roles."""
    chat = _chat()

    for role in ("user", "assistant", "system", "tool"):
        msg = chat.ChatMessage(role=role, content="hello")
        assert msg.role == role
        assert msg.content == "hello"


def test_chat_message_rejects_invalid_role():
    """ChatMessage with invalid role raises ValidationError."""
    chat = _chat()

    with pytest.raises(ValidationError, match="role"):
        chat.ChatMessage.model_validate({"role": "invalid"})


def test_chat_request_validates_well_formed_body():
    """Well-formed body validates with correct field values and None defaults."""
    chat = _chat()

    request = chat.ChatRequest.model_validate({"messages": [{"role": "user", "content": "hello"}]})

    assert len(request.messages) == 1
    assert request.messages[0].role == "user"
    assert request.messages[0].content == "hello"

    # All control fields default to None
    assert request.session_id is None
    assert request.execution_mode is None
    assert request.repo_url is None
    assert request.repo_ref is None
    assert request.context_paths is None
    assert request.batch_concurrency is None
    assert request.docs_path is None
    assert request.trace is None
    assert request.trace_mode is None
    assert request.selected_skill_ids is None


def test_chat_request_rejects_empty_messages():
    """ChatRequest.model_validate with empty messages raises ValidationError."""
    chat = _chat()

    with pytest.raises(ValidationError):
        chat.ChatRequest.model_validate({"messages": []})


def test_chat_request_rejects_missing_messages():
    """ChatRequest.model_validate with missing messages raises ValidationError."""
    chat = _chat()

    with pytest.raises(ValidationError):
        chat.ChatRequest.model_validate({})


def test_chat_request_rejects_unknown_fields():
    """ChatRequest with extra='forbid' rejects unknown top-level fields."""
    chat = _chat()

    with pytest.raises(ValidationError, match="bogus"):
        chat.ChatRequest.model_validate({"messages": [{"role": "user", "content": "hi"}], "bogus": 1})


def test_chat_request_messages_only_valid():
    """Messages-only ChatRequest validates with all control fields None."""
    chat = _chat()

    request = chat.ChatRequest.model_validate({"messages": [{"role": "user", "content": "test"}]})

    assert len(request.messages) == 1
    assert request.messages[0].content == "test"
    assert request.session_id is None
    assert request.execution_mode is None
    assert request.repo_url is None
    assert request.repo_ref is None
    assert request.context_paths is None
    assert request.batch_concurrency is None
    assert request.docs_path is None
    assert request.trace is None
    assert request.trace_mode is None
    assert request.selected_skill_ids is None


def test_legacy_execution_modes_accepted():
    """Legacy execution_mode values (auto/rlm_only/tools_only) validate without error."""
    chat = _chat()

    for mode in ("auto", "rlm_only", "tools_only"):
        request = chat.ChatRequest.model_validate(
            {"messages": [{"role": "user", "content": "hi"}], "execution_mode": mode}
        )
        assert request.execution_mode == mode


def test_chat_message_with_parts_and_no_content():
    """ChatMessage with parts and content=None is valid (AI SDK UIMessage shape)."""
    chat = _chat()

    msg = chat.ChatMessage.model_validate(
        {
            "role": "user",
            "parts": [{"type": "text", "text": "hello"}],
            "content": None,
        }
    )
    assert msg.role == "user"
    assert msg.content is None
    assert msg.parts == [{"type": "text", "text": "hello"}]


def test_chat_request_extra_forbid_on_unknown_message_field():
    """ChatMessage uses extra='forbid' so unknown message fields raise."""
    chat = _chat()

    with pytest.raises(ValidationError):
        chat.ChatMessage.model_validate({"role": "user", "content": "hi", "unknown_field": "x"})


def test_chat_request_with_control_fields():
    """ChatRequest with all control fields populated validates correctly."""
    chat = _chat()

    request = chat.ChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "session_id": "sess-1",
            "execution_mode": "auto",
            "repo_url": "https://github.com/example/repo",
            "repo_ref": "main",
            "context_paths": ["src/"],
            "batch_concurrency": 3,
            "docs_path": "/docs",
            "trace": True,
            "trace_mode": "verbose",
            "selected_skill_ids": ["skill-1", "skill-2"],
        }
    )

    assert request.session_id == "sess-1"
    assert request.execution_mode == "auto"
    assert request.repo_url == "https://github.com/example/repo"
    assert request.repo_ref == "main"
    assert request.context_paths == ["src/"]
    assert request.batch_concurrency == 3
    assert request.docs_path == "/docs"
    assert request.trace is True
    assert request.trace_mode == "verbose"
    assert request.selected_skill_ids == ["skill-1", "skill-2"]


def test_chat_message_default_content_is_none():
    """ChatMessage with only role defaults content and parts to None."""
    chat = _chat()

    msg = chat.ChatMessage(role="user")
    assert msg.content is None
    assert msg.parts is None


def test_chat_message_content_accepts_string():
    """ChatMessage content accepts a plain string."""
    chat = _chat()

    msg = chat.ChatMessage(role="user", content="hello world")
    assert msg.content == "hello world"


def test_chat_message_parts_accepts_list_of_dicts():
    """ChatMessage parts accepts a list of dicts."""
    chat = _chat()

    msg = chat.ChatMessage(
        role="assistant",
        parts=[{"type": "text", "text": "response"}],
    )
    assert msg.parts == [{"type": "text", "text": "response"}]
    assert msg.content is None


def test_chat_request_extra_forbid_on_chat_message_unknown_field():
    """ChatMessage extra='forbid' catches unknown fields at message level."""
    chat = _chat()

    with pytest.raises(ValidationError):
        chat.ChatRequest.model_validate({"messages": [{"role": "user", "content": "hi", "invalid_field": True}]})


def test_chat_request_rejects_null_messages():
    """ChatRequest with messages=None raises ValidationError."""
    chat = _chat()

    with pytest.raises(ValidationError):
        chat.ChatRequest.model_validate({"messages": None})


def test_chat_request_rejects_non_list_messages():
    """ChatRequest with messages as non-list raises ValidationError."""
    chat = _chat()

    with pytest.raises(ValidationError):
        chat.ChatRequest.model_validate({"messages": "not-a-list"})
