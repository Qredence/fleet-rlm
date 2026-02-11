from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from dspy.primitives.code_interpreter import FinalOutput

pytest.importorskip("textual")

from fleet_rlm.interactive.models import SessionConfig, StreamEvent
from fleet_rlm.interactive.textual_app import CodeChatTextualApp


class _FakeInterpreter:
    def execute(self, code: str):
        return FinalOutput({"ok": True, "code": code})


class _FakeAgent:
    def __init__(self) -> None:
        self.history = SimpleNamespace(messages=[])
        self.react_tools = [self.load_document]
        self.interpreter = _FakeInterpreter()

    def reset(self, *, clear_sandbox_buffers: bool = True):
        self.history.messages = []
        return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

    def load_document(self, path: str, alias: str = "active"):
        return {"status": "ok", "path": path, "alias": alias}

    def list_documents(self):
        return {"documents": [{"alias": "active"}], "active_alias": "active"}

    def save_buffer_to_volume(self, name: str, path: str):
        return {"status": "ok", "name": name, "path": path}

    def load_text_from_volume(self, path: str, alias: str = "active"):
        return {"status": "ok", "path": path, "alias": alias}

    def chat_turn(self, message: str):
        output = f"echo:{message}"
        self.history.messages.append({"user_request": message, "assistant_response": output})
        return {"assistant_response": output, "trajectory": {}, "history_turns": len(self.history.messages)}

    def iter_chat_turn_stream(self, message: str, trace: bool, cancel_check=None):
        self.history.messages.append({"user_request": message, "assistant_response": "stream"})
        yield StreamEvent(kind="status", text="Calling tool: fake_tool")
        yield StreamEvent(kind="assistant_token", text="hello ")
        if trace:
            yield StreamEvent(kind="reasoning_step", text="thinking", payload={"source": "next_thought"})
        if cancel_check is not None and cancel_check():
            yield StreamEvent(
                kind="cancelled",
                text="hello [cancelled]",
                payload={"history_turns": len(self.history.messages)},
            )
            return
        yield StreamEvent(kind="assistant_token", text="world")
        yield StreamEvent(kind="status", text="Tool finished.")
        yield StreamEvent(
            kind="final",
            text="hello world",
            payload={"trajectory": {"tool_name_0": "fake_tool"}, "history_turns": len(self.history.messages)},
        )


class _SlowCancelAgent(_FakeAgent):
    def iter_chat_turn_stream(self, message: str, trace: bool, cancel_check=None):
        self.history.messages.append({"user_request": message, "assistant_response": "stream"})
        for _ in range(200):
            if cancel_check is not None and cancel_check():
                yield StreamEvent(
                    kind="cancelled",
                    text="partial [cancelled]",
                    payload={"history_turns": len(self.history.messages)},
                )
                return
            yield StreamEvent(kind="assistant_token", text="x")
            time.sleep(0.01)
        yield StreamEvent(
            kind="final",
            text="x" * 200,
            payload={"history_turns": len(self.history.messages)},
        )


@pytest.mark.anyio
async def test_textual_stream_updates_state_and_transcript():
    app = CodeChatTextualApp(
        agent=_FakeAgent(),
        config=SessionConfig(profile_name="test", stream=True, trace_mode="compact"),
    )
    async with app.run_test() as pilot:
        await pilot.press("h", "i", "enter")
        await asyncio.sleep(0.25)

        assert app._turn_state is not None
        assert app._turn_state.final_text == "hello world"
        assert app._turn_state.status_messages
        assert app._in_flight is False


@pytest.mark.anyio
async def test_textual_reasoning_updates_in_verbose_mode():
    app = CodeChatTextualApp(
        agent=_FakeAgent(),
        config=SessionConfig(profile_name="test", stream=True, trace_mode="verbose"),
    )
    async with app.run_test() as pilot:
        await pilot.press("h", "i", "enter")
        await asyncio.sleep(0.25)

        assert app._turn_state is not None
        assert "thinking" in app._turn_state.reasoning_lines


@pytest.mark.anyio
async def test_textual_ctrl_c_cancels_in_flight_turn():
    app = CodeChatTextualApp(
        agent=_SlowCancelAgent(),
        config=SessionConfig(profile_name="test", stream=True, trace_mode="compact"),
    )
    async with app.run_test() as pilot:
        await pilot.press("h", "i", "enter")
        await asyncio.sleep(0.1)
        app.action_cancel_turn()
        await asyncio.sleep(0.4)

        assert app._turn_state is not None
        assert app._turn_state.cancelled is True
        assert app._in_flight is False


@pytest.mark.anyio
async def test_textual_slash_commands_toggle_modes():
    app = CodeChatTextualApp(
        agent=_FakeAgent(),
        config=SessionConfig(profile_name="test", stream=True, trace_mode="compact"),
    )
    async with app.run_test() as pilot:
        await pilot.press("/", "t", "r", "a", "c", "e", " ", "v", "e", "r", "b", "o", "s", "e", "enter")
        await asyncio.sleep(0.05)
        assert app.trace_mode == "verbose"

        await pilot.press("/", "s", "t", "r", "e", "a", "m", " ", "o", "f", "f", "enter")
        await asyncio.sleep(0.05)
        assert app.stream_enabled is False

        await pilot.press("/", "c", "l", "e", "a", "r", "enter")
        await asyncio.sleep(0.05)
        assert str(app.query_one("#live_stream").content) == ""
