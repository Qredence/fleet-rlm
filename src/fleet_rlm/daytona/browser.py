"""One Run's native Chromium launch identity and cleanup fence."""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import dspy

from fleet_rlm.tool_events import ToolEventView


class BrowserUnavailableError(RuntimeError):
    public_message = "Run browser is unavailable"


@dataclass(slots=True)
class RunBrowser:
    sandbox: Any
    scratch: str
    deadline: float
    is_authorized: Callable[[], bool]
    mark_tainted: Callable[[], None]
    _attempted: bool = field(default=False, init=False)
    _groups: dict[str, str] | None = field(default=None, init=False)
    _binding: dict[str, Any] | None = field(default=None, init=False)

    def open_browser(self) -> dict[str, Any]:
        """Launch once; retain process identities on the host before returning."""
        if not self.is_authorized() or time.monotonic() >= self.deadline:
            raise BrowserUnavailableError("Run browser is no longer authorized")
        if self._binding is not None:
            return dict(self._binding)
        if self._attempted:
            raise BrowserUnavailableError("Run browser launch remains unresolved")
        self._attempted = True
        try:
            result = self.sandbox.process.exec(
                shlex.join(["python", "/opt/fleet/browser_session.py", "start", self.scratch]),
                timeout=min(25, max(1, int(self.deadline - time.monotonic()))),
            )
            if result.exit_code != 0:
                raise BrowserUnavailableError("Run browser launch failed")
            payload = json.loads(result.result)
            groups = payload.get("process_groups")
            endpoint = urlsplit(payload["cdp_url"])
            if (
                not isinstance(groups, dict)
                or not groups
                or any(
                    not str(pid).isdecimal() or int(pid) < 1 or not str(started).isdecimal()
                    for pid, started in groups.items()
                )
                or endpoint.scheme != "http"
                or endpoint.hostname != "127.0.0.1"
                or endpoint.port is None
                or payload["session"] != f"fleet-{self.scratch.rsplit('/', 1)[-1]}"
            ):
                raise BrowserUnavailableError("Run browser launch returned invalid ownership")
            self._groups = dict(groups)
            self._binding = {
                "command": ["agent-browser", "--session", payload["session"], "--cdp", payload["cdp_url"]],
                "environment": {"FLEET_BROWSER_RUN": self.scratch.rsplit("/", 1)[-1]},
            }
            if not self.is_authorized() or time.monotonic() >= self.deadline:
                raise BrowserUnavailableError("Run browser is no longer authorized")
            return dict(self._binding)
        except Exception:
            self.mark_tainted()
            raise BrowserUnavailableError("Run browser launch failed safely") from None

    def close(self) -> None:
        if not self._attempted:
            return
        if self._groups is None:
            self.mark_tainted()
            raise BrowserUnavailableError("Run browser launch ownership remains unresolved")
        try:
            result = self.sandbox.process.exec(
                shlex.join(
                    [
                        "python",
                        "/opt/fleet/browser_session.py",
                        "close",
                        self.scratch,
                        "--groups",
                        json.dumps(self._groups),
                    ]
                ),
                timeout=10,
            )
            if result.exit_code != 0 or str(result.result).strip() != "fleet-browser-cleanup-ok":
                raise BrowserUnavailableError("Run browser cleanup remains unresolved")
            self._binding = None
            self._groups = None
            self._attempted = False
        except Exception:
            self.mark_tainted()
            raise BrowserUnavailableError("Run browser cleanup failed safely") from None

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        return (
            dspy.Tool(
                self.open_browser,
                name="open_browser",
                desc=(
                    "Open this Run's fresh sandboxed Chromium once. Returns command and environment for direct "
                    "agent-browser subprocess calls. Use for public research; reuse the prefix and environment."
                ),
            ),
        )

    def event_views(self) -> dict[str, ToolEventView]:
        return {"open_browser": ToolEventView.metadata_only()}
