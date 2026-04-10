"""Session helpers for the outer orchestration layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .checkpoints import OrchestrationCheckpointState


@dataclass(slots=True)
class OrchestrationSessionContext:
    """Minimal workflow/session context shared across orchestration seams."""

    workspace_id: str | None
    user_id: str | None
    session_id: str | None
    session_record: dict[str, Any] | None

    @classmethod
    def from_session_record(
        cls,
        session_record: dict[str, Any] | None,
    ) -> OrchestrationSessionContext | None:
        if not isinstance(session_record, dict):
            return None
        return cls(
            workspace_id=str(session_record.get("workspace_id", "")).strip() or None,
            user_id=str(session_record.get("user_id", "")).strip() or None,
            session_id=str(session_record.get("session_id", "")).strip() or None,
            session_record=session_record,
        )

    def load_checkpoint_state(self) -> OrchestrationCheckpointState:
        if not isinstance(self.session_record, dict):
            return OrchestrationCheckpointState()
        candidate = self.session_record.get("orchestration")
        if isinstance(candidate, dict):
            return OrchestrationCheckpointState.from_dict(candidate)
        return OrchestrationCheckpointState.from_dict(
            self._manifest_metadata().get("orchestration")
        )

    def save_checkpoint_state(self, state: OrchestrationCheckpointState) -> None:
        if not isinstance(self.session_record, dict):
            return
        serialized = state.to_dict()
        self.session_record["orchestration"] = serialized
        self._manifest_metadata()["orchestration"] = serialized

    def _manifest_metadata(self) -> dict[str, Any]:
        if not isinstance(self.session_record, dict):
            return {}
        manifest = self.session_record.get("manifest")
        if not isinstance(manifest, dict):
            manifest = {}
            self.session_record["manifest"] = manifest
        metadata = manifest.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            manifest["metadata"] = metadata
        return metadata
