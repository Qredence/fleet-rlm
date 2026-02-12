"""Prompt-toolkit based interactive coding session for ReAct + RLM."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any

from dspy.primitives.code_interpreter import FinalOutput
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from tenacity import retry, stop_after_attempt, wait_exponential

from fleet_rlm.react import RLMReActChatAgent

from .config import get_profile, set_active_profile
from .models import SessionConfig, TranscriptEvent
from .ui import ChatUI


class CodeChatSession:
    """Interactive REPL session that routes commands and chat to RLMReActChatAgent."""

    def __init__(
        self,
        *,
        agent: RLMReActChatAgent,
        config: SessionConfig,
        ui: ChatUI | None = None,
    ) -> None:
        self.agent = agent
        self.config = config
        self.ui = ui or ChatUI()
        self.trace = config.trace
        self.stream = config.stream

        history_dir = Path.home() / ".cache" / "fleet-rlm"
        history_dir.mkdir(parents=True, exist_ok=True)
        self.prompt = PromptSession(
            history=FileHistory(str(history_dir / "code-chat.history")),
            multiline=False,
            enable_history_search=True,
        )

        self.log_path = history_dir / "logs" / "code-chat.log"
        self.transcript_path = history_dir / "transcripts" / "latest.jsonl"
        self._configure_logging()

    def _configure_logging(self) -> None:
        from loguru import logger

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.remove()
        logger.add(self.log_path, rotation="10 MB", retention=5, level="INFO")

    async def _append_transcript_async(self, event: TranscriptEvent) -> None:
        import aiofiles

        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self.transcript_path, "a", encoding="utf-8") as f:
            await f.write(event.model_dump_json(ensure_ascii=False) + "\n")

    def _append_transcript(self, event: TranscriptEvent) -> None:
        try:
            asyncio.run(self._append_transcript_async(event))
        except RuntimeError:
            # Running event loop edge-case fallback.
            with open(self.transcript_path, "a", encoding="utf-8") as f:
                f.write(event.model_dump_json(ensure_ascii=False) + "\n")

    @retry(
        wait=wait_exponential(multiplier=0.5, min=0.5, max=3),
        stop=stop_after_attempt(3),
    )
    def _chat_once(self, message: str) -> dict[str, Any]:
        if self.stream:
            return self.agent.chat_turn_stream(message=message, trace=self.trace)
        return self.agent.chat_turn(message)

    def run(self) -> None:
        """Run interactive loop until user exits."""
        self.ui.banner(
            profile_name=self.config.profile_name,
            trace=self.trace,
            stream=self.stream,
        )

        while True:
            try:
                raw = self.prompt.prompt("you> ")
            except (EOFError, KeyboardInterrupt):
                self.ui.info("Exiting code-chat.")
                break

            text = raw.strip()
            if not text:
                continue

            if text.startswith("/"):
                should_exit = self._handle_command(text)
                if should_exit:
                    break
                continue

            self._append_transcript(TranscriptEvent(role="user", content=text))
            try:
                result = self._chat_once(text)
            except Exception as exc:
                self.ui.error(f"Chat error: {exc}")
                self._append_transcript(
                    TranscriptEvent(role="system", content=f"error: {exc}")
                )
                continue

            assistant_text = result.get("assistant_response", "")
            self.ui.assistant(assistant_text)
            self._append_transcript(
                TranscriptEvent(role="assistant", content=str(assistant_text))
            )

            if self.trace:
                self.ui.trace(result.get("trajectory", {}))
                self._append_transcript(
                    TranscriptEvent(
                        role="trace",
                        payload={"trajectory": result.get("trajectory", {})},
                    )
                )

    def _handle_command(self, raw: str) -> bool:
        parts = shlex.split(raw[1:])
        if not parts:
            return False

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "exit":
            self.ui.info("Exiting code-chat.")
            return True
        if cmd == "help":
            self.ui.show_help()
            return False
        if cmd == "history":
            self.ui.data("history", {"messages": self.agent.history.messages})
            return False
        if cmd == "reset":
            self.ui.data("reset", self.agent.reset(clear_sandbox_buffers=True))
            return False
        if cmd == "tools":
            tool_names = [
                getattr(tool, "__name__", str(tool)) for tool in self.agent.react_tools
            ]
            self.ui.data("tools", {"tools": tool_names})
            return False
        if cmd == "load":
            if len(args) < 1:
                self.ui.error("Usage: /load <path>")
                return False
            self.ui.data("load", self.agent.load_document(args[0], alias="active"))
            return False
        if cmd == "docs":
            self.ui.data("documents", self.agent.list_documents())
            return False
        if cmd == "trace":
            if len(args) != 1 or args[0] not in {"on", "off"}:
                self.ui.error("Usage: /trace on|off")
                return False
            self.trace = args[0] == "on"
            self.ui.info(f"trace={self.trace}")
            return False
        if cmd == "profile":
            if len(args) == 0 or args[0] == "show":
                profile = get_profile()
                self.ui.data("profile", profile.model_dump())
                return False
            if args[0] == "set" and len(args) == 2:
                profile = set_active_profile(args[1])
                self.ui.data(
                    "profile",
                    {
                        "active_profile": profile.name,
                        "note": "Profile updated. Restart session to fully apply settings.",
                    },
                )
                return False
            self.ui.error("Usage: /profile show|set <name>")
            return False
        if cmd == "py":
            code = self._collect_python_block()
            if not code.strip():
                self.ui.info("No code provided.")
                return False
            result = self.agent.interpreter.execute(code)
            if isinstance(result, FinalOutput):
                self.ui.data("python-result", result.output)
            else:
                self.ui.data("python-output", {"output": str(result)})
            return False
        if cmd == "rg":
            if len(args) < 1:
                self.ui.error("Usage: /rg <pattern> [path]")
                return False
            search_path = args[1] if len(args) > 1 else "."
            self.ui.data("rg", self._run_rg(args[0], search_path))
            return False
        if cmd == "save-buffer":
            if len(args) != 2:
                self.ui.error("Usage: /save-buffer <name> <path>")
                return False
            self.ui.data(
                "save-buffer", self.agent.save_buffer_to_volume(args[0], args[1])
            )
            return False
        if cmd == "load-volume":
            if len(args) < 1:
                self.ui.error("Usage: /load-volume <path> [alias]")
                return False
            alias = args[1] if len(args) > 1 else "active"
            self.ui.data(
                "load-volume", self.agent.load_text_from_volume(args[0], alias=alias)
            )
            return False

        self.ui.error(f"Unknown command: /{cmd}. Type /help.")
        return False

    def _collect_python_block(self) -> str:
        self.ui.info("Enter Python code. Finish with a line containing only ':end'.")
        lines: list[str] = []
        while True:
            try:
                line = self.prompt.prompt("py> ")
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip() == ":end":
                break
            lines.append(line)
        return "\n".join(lines)

    def _run_rg(self, pattern: str, path: str) -> dict[str, Any]:
        from ripgrepy import Ripgrepy

        rg = Ripgrepy(pattern, path).json().with_filename().line_number().max_count(50)
        out = rg.run()
        hits = []
        for item in out.as_dict:
            if item.get("type") != "match":
                continue
            data = item.get("data", {})
            path_text = data.get("path", {}).get("text", "")
            line_no = data.get("line_number")
            line_text = data.get("lines", {}).get("text", "").rstrip("\n")
            hits.append({"path": path_text, "line": line_no, "text": line_text})
        return {"pattern": pattern, "path": path, "count": len(hits), "hits": hits[:20]}
