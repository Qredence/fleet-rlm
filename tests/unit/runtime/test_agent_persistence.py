from __future__ import annotations

import dspy

from fleet_rlm.runtime.agent.persistence import export_session, import_session


class _Runtime:
    def __init__(self) -> None:
        self.history = dspy.History(messages=[{"user_message": "hi", "response": "hello"}])
        self.core_memory = {"persona": "assistant", "human": "developer", "scratchpad": ""}
        self.loaded_document_paths = ["/docs/one.md"]
        self.conversation_summary = "compressed context"
        self._turns_since_summary = 3

    def default_core_memory(self) -> dict[str, str]:
        return {"persona": "", "human": "", "scratchpad": ""}


def test_export_import_preserves_summary_and_document_paths() -> None:
    source = _Runtime()
    payload = export_session(source, "session-1")
    target = _Runtime()
    target.loaded_document_paths = []
    target.conversation_summary = ""
    target._turns_since_summary = 0

    summary = import_session(target, payload)

    assert summary["status"] == "ok"
    assert target.loaded_document_paths == ["/docs/one.md"]
    assert target.conversation_summary == "compressed context"
    assert target._turns_since_summary == 3
