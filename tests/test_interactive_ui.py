from __future__ import annotations

from io import StringIO

from rich.console import Console

from fleet_rlm.interactive.ui import ChatUI


def _rendered_output() -> tuple[ChatUI, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=100)
    return ChatUI(console=console), buf


def test_chat_ui_renders_banner_and_help():
    ui, buf = _rendered_output()

    ui.banner(profile_name="default", trace=True, stream=False)
    ui.show_help()

    output = buf.getvalue()
    assert "fleet-rlm code-chat" in output
    assert "trace=True" in output
    assert "/py - execute multiline python" in output


def test_chat_ui_renders_assistant_trace_and_error():
    ui, buf = _rendered_output()

    ui.assistant("# Hello\nThis is **markdown**")
    ui.trace({"tool": "load_document", "ok": True})
    ui.error("something failed")

    output = buf.getvalue()
    assert "assistant" in output
    assert "Hello" in output
    assert "load_document" in output
    assert "something failed" in output
