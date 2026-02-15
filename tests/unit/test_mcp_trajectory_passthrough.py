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


def test_mcp_additive_tools_register_and_forward(monkeypatch):
    class _FakeAgent:
        def grounded_answer(self, **kwargs):
            return {"status": "ok", "tool": "grounded_answer", **kwargs}

        def triage_incident_logs(self, **kwargs):
            return {"status": "ok", "tool": "triage_incident_logs", **kwargs}

        def memory_tree(self, **kwargs):
            return {"status": "ok", "tool": "memory_tree", **kwargs}

        def memory_structure_audit(self, **kwargs):
            return {"status": "ok", "tool": "memory_structure_audit", **kwargs}

        def clarification_questions(self, **kwargs):
            return {"status": "ok", "tool": "clarification_questions", **kwargs}

    class _FakeCtx:
        def __enter__(self):
            return _FakeAgent()

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=_FakeFastMCP))
    monkeypatch.setattr(
        "fleet_rlm.mcp.server.runners.build_react_chat_agent",
        lambda **kwargs: _FakeCtx(),
    )

    server = create_mcp_server()
    grounded = server.tools["grounded_answer"]
    triage = server.tools["triage_incident_logs"]
    memory_tree = server.tools["memory_tree"]
    memory_audit = server.tools["memory_structure_audit"]
    clarification = server.tools["clarification_questions"]

    grounded_result = grounded("a.txt", "q", max_chunks=3, include_trajectory=False)
    triage_result = triage("a.txt", "why?", service_context="prod")
    tree_result = memory_tree(root_path="/data/memory", max_depth=3)
    audit_result = memory_audit(usage_goals="organization")
    clarification_result = clarification(request="clean memory", operation_risk="high")

    assert grounded_result["tool"] == "grounded_answer"
    assert grounded_result["max_chunks"] == 3
    assert grounded_result["include_trajectory"] is False
    assert triage_result["tool"] == "triage_incident_logs"
    assert triage_result["service_context"] == "prod"
    assert tree_result["tool"] == "memory_tree"
    assert tree_result["max_depth"] == 3
    assert audit_result["tool"] == "memory_structure_audit"
    assert clarification_result["tool"] == "clarification_questions"
