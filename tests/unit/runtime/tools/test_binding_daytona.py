from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def test_recursive_workspace_binding_uses_current_daytona_evidence_sink(monkeypatch) -> None:
    from fleet_rlm.runtime.modules import workspace as workspace_module
    from fleet_rlm.runtime.tools.binding import _bound_runtime_tool_factories

    captured: dict[str, Any] = {}

    class _FakeRecursiveWorkspaceModule:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __call__(self, *, user_request: str, context: str) -> object:
            captured["user_request"] = user_request
            captured["context"] = context
            return SimpleNamespace(status="ok", answer="done", passes=1)

    monkeypatch.setattr(workspace_module, "RecursiveWorkspaceModule", _FakeRecursiveWorkspaceModule)

    interpreter = SimpleNamespace(sub_lm=None)
    factories = _bound_runtime_tool_factories(runtime=SimpleNamespace(core_memory={}), interpreter=interpreter)

    result = factories["recursive_workspace"]("analyze", context="ctx")

    assert result == {"status": "ok", "answer": "done", "passes": 1}
    assert captured["interpreter"] is interpreter
    assert type(captured["evidence_sink"]).__name__ == "DaytonaEvidenceSink"
