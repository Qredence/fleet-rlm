from __future__ import annotations

import sys
from types import SimpleNamespace

from fleet_rlm.mcp.server import create_mcp_server


class _FakeFastMCP:
    def __init__(self, name: str):
        self.name = name
        self.tools: dict[str, callable] = {}

    def tool(self, fn):
        self.tools[fn.__name__] = fn
        return fn


def test_mcp_long_document_tools_forward_include_trajectory(monkeypatch):
    calls = []

    def _fake_run_long_context(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=_FakeFastMCP))
    monkeypatch.setattr(
        "fleet_rlm.mcp.server.runners.run_long_context", _fake_run_long_context
    )

    server = create_mcp_server()
    analyze = server.tools["analyze_long_document"]
    summarize = server.tools["summarize_long_document"]

    analyze("a.txt", "q")
    summarize("a.txt", "q2", include_trajectory=False)

    assert calls[0]["include_trajectory"] is True
    assert calls[1]["include_trajectory"] is False
