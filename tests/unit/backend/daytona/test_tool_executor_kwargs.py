"""RC-1 regression: DSPy interpreter tools are kwargs-only; broker forwards by name.

Pinned DSPy 3.3.x ``dspy.predict.rlm.RLM._make_interpreter_tool`` wraps every
user Tool in ``def invoke(**kwargs)`` while spoofing ``__signature__``. Fleet's
host ``tool_executor`` applies the broker payload as ``fn(*args, **kwargs)``, so
any positional entry in the wire ``args`` list TypeErrors before the Tool body
runs. These tests drive the real pinned-DSPy wrapping and Fleet's real wrapper
generation to lock the kwargs-only contract end to end.
"""

from __future__ import annotations

import inspect
import json
import urllib.request
from collections.abc import Callable
from uuid import uuid4

import dspy
import pytest
from dspy.predict.rlm import RLM

from fleet_rlm.artifacts.tools import ArtifactToolHost
from fleet_rlm.attachments.tools import AttachmentToolHost
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
from fleet_rlm.sessions.models import SessionHistory
from fleet_rlm.skills.tools import SkillToolHost
from fleet_rlm.workspace.memory import WorkspaceMemoryToolHost
from fleet_rlm.workspace.projects import ProjectToolHost
from fleet_rlm.workspace.url import UrlToolHost
from fleet_rlm.workspace.workspace import WorkspaceToolHost


def _write_workspace_text(path: str, content: str, overwrite: bool = False) -> dict[str, object]:
    """Mirror the ``write_workspace_text`` host tool surface."""
    return {"ok": True, "path": path, "content": content, "overwrite": overwrite}


def _list_workspace_files(path: str = ".", limit: int = 100, after: str | None = None) -> dict[str, object]:
    """Mirror the ``list_workspace_files`` host tool surface (all defaults)."""
    return {"ok": True, "path": path, "limit": limit, "after": after}


class _StubbedHTTPResponse:
    """Minimal context-manager response returned by the stubbed ``urlopen``."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _StubbedHTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _build_sandbox_wrapper(tool_name: str, tool_func: Callable[..., object]) -> Callable[..., object]:
    broker = DaytonaHttpToolBroker(sandbox=object())
    namespace: dict[str, object] = {}
    exec(broker._tool_wrapper_source(tool_name, tool_func), namespace, namespace)
    wrapper = namespace[tool_name]
    assert callable(wrapper)
    return wrapper


def _capture_wire_payloads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _StubbedHTTPResponse:
        del timeout
        captured.append(json.loads(bytes(request.data).decode("utf-8")))
        return _StubbedHTTPResponse({"result": None})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_dspy_interpreter_tool_survives_kwargs_only_host_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end RC-1 regression over the real pinned-DSPy callables."""
    tool = dspy.Tool(_write_workspace_text, name="write_workspace_text")
    rlm = RLM("prompt -> answer", max_iters=1)
    invoke = rlm._make_interpreter_tool(tool)

    # The spoofed signature keeps model-facing ergonomics while the callable
    # body accepts keyword arguments only.
    signature = inspect.signature(invoke)
    assert list(signature.parameters) == ["path", "content", "overwrite"]
    assert signature.parameters["overwrite"].default is False
    with pytest.raises(TypeError, match="positional"):
        invoke("notes/todo.md", "hello fleet")

    wrapper = _build_sandbox_wrapper("write_workspace_text", invoke)
    captured = _capture_wire_payloads(monkeypatch)

    wrapper("notes/todo.md", "hello fleet", overwrite=True)

    assert len(captured) == 1
    payload = captured[0]
    assert payload["args"] == []
    assert payload["kwargs"] == {
        "path": "notes/todo.md",
        "content": "hello fleet",
        "overwrite": True,
    }

    # Fleet's host tool_executor applies the payload as ``fn(*args, **kwargs)``;
    # the kwargs-only wire shape reaches the real Tool body intact.
    result = invoke(*payload["args"], **payload["kwargs"])
    assert result == {
        "ok": True,
        "path": "notes/todo.md",
        "content": "hello fleet",
        "overwrite": True,
    }


def test_all_default_parameters_tool_remains_kwargs_only(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = dspy.Tool(_list_workspace_files, name="list_workspace_files")
    rlm = RLM("prompt -> answer", max_iters=1)
    invoke = rlm._make_interpreter_tool(tool)

    wrapper = _build_sandbox_wrapper("list_workspace_files", invoke)
    captured = _capture_wire_payloads(monkeypatch)

    wrapper("reports")

    assert len(captured) == 1
    payload = captured[0]
    assert payload["args"] == []
    assert payload["kwargs"] == {"path": "reports", "limit": 100, "after": None}

    result = invoke(*payload["args"], **payload["kwargs"])
    assert result == {"ok": True, "path": "reports", "limit": 100, "after": None}


def test_host_tool_surface_declares_no_positional_only_parameters() -> None:
    """The kwargs-only wire contract holds for every current host tool."""
    hosts = (
        WorkspaceToolHost(None, max_file_bytes=1024),  # type: ignore[arg-type]
        WorkspaceMemoryToolHost(None),  # type: ignore[arg-type]
        ProjectToolHost(None, max_file_bytes=1024),  # type: ignore[arg-type]
        UrlToolHost(session_id=uuid4(), store=None, max_bytes=1024),  # type: ignore[arg-type]
        AttachmentToolHost(
            attachments=(),
            staged_attachments=(),
            volume_fs=None,  # type: ignore[arg-type]
        ),
        ArtifactToolHost(
            volume_fs=None,  # type: ignore[arg-type]
            user_id=uuid4(),
            workspace_id=uuid4(),
            session_id=uuid4(),
            run_id=uuid4(),
        ),
        SessionHistoryToolHost(SessionHistory()),
        SkillToolHost(None),  # type: ignore[arg-type]
    )
    tools = [tool for host in hosts for tool in host.as_tools()]

    assert tools, "host surface enumeration regressed: no tools collected"
    positional_only = [
        f"{tool.name}.{parameter.name}"
        for tool in tools
        for parameter in inspect.signature(tool.func).parameters.values()
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY
    ]
    assert positional_only == []
