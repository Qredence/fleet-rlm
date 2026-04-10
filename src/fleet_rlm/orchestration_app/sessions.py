"""Session helpers for the outer orchestration layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..api.dependencies import ServerState, session_key
from ..api.server_utils import owner_fingerprint
from ..api.server_utils import sanitize_id as _sanitize_id
from .checkpoints import (
    ContinuationCheckpoint,
    OrchestrationCheckpointState,
    PendingApprovalCheckpoint,
    WorkflowStage,
)

if TYPE_CHECKING:
    from ..api.routers.ws.types import ChatAgentProtocol, LocalPersistFn


@dataclass(slots=True)
class SessionRecordLink:
    """Stable linkage from orchestration state back to the backing session record."""

    key: str | None = None
    manifest_path: str | None = None
    db_session_id: str | None = None


def _resolved_manifest_path(
    *,
    workspace_id: str | None,
    user_id: str | None,
    session_id: str | None,
) -> str | None:
    if not workspace_id or not user_id or not session_id:
        return None
    safe_session_id = _sanitize_id(session_id, "default-session")
    return (
        f"meta/workspaces/{workspace_id}/users/{user_id}/"
        f"react-session-{safe_session_id}.json"
    )


def _switch_manifest_path(*, owner_id: str, workspace_id: str, session_id: str) -> str:
    """Preserve the existing websocket manifest layout used during session switch."""

    manifest_path = _resolved_manifest_path(
        workspace_id=owner_id,
        user_id=workspace_id,
        session_id=session_id,
    )
    if manifest_path is None:
        raise ValueError("owner_id, workspace_id, and session_id are required")
    return manifest_path


def _build_continuation_state_metadata(
    *,
    continuation: ContinuationCheckpoint | None,
    pending_approval: PendingApprovalCheckpoint | None,
) -> dict[str, Any]:
    if continuation is None and pending_approval is None:
        return {}
    metadata = continuation.to_dict() if continuation is not None else {}
    if pending_approval is not None:
        metadata.setdefault("message_id", pending_approval.message_id)
        metadata["pending_action_labels"] = list(pending_approval.action_labels or [])
        metadata["pending_question"] = pending_approval.question
        metadata["pending_source"] = pending_approval.source
    return metadata


@dataclass(slots=True)
class OrchestrationSessionContext:
    """Authoritative workflow/session context shared across orchestration seams."""

    workspace_id: str | None
    user_id: str | None
    session_id: str | None
    session_record: dict[str, Any] | None
    session_record_link: SessionRecordLink = field(default_factory=SessionRecordLink)
    workflow_stage: WorkflowStage = "idle"
    pending_approval: PendingApprovalCheckpoint | None = None
    continuation: ContinuationCheckpoint | None = None
    continuation_token: str | None = None
    continuation_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_session_record(
        cls,
        session_record: dict[str, Any] | None,
        *,
        workspace_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        key: str | None = None,
        manifest_path: str | None = None,
    ) -> OrchestrationSessionContext | None:
        if not isinstance(session_record, dict):
            return None
        context = cls(
            workspace_id=workspace_id
            or str(session_record.get("workspace_id", "")).strip()
            or None,
            user_id=user_id or str(session_record.get("user_id", "")).strip() or None,
            session_id=session_id
            or str(session_record.get("session_id", "")).strip()
            or None,
            session_record=session_record,
            session_record_link=SessionRecordLink(
                key=key or str(session_record.get("key", "")).strip() or None,
                manifest_path=manifest_path
                or _resolved_manifest_path(
                    workspace_id=workspace_id
                    or str(session_record.get("workspace_id", "")).strip()
                    or None,
                    user_id=user_id
                    or str(session_record.get("user_id", "")).strip()
                    or None,
                    session_id=session_id
                    or str(session_record.get("session_id", "")).strip()
                    or None,
                ),
                db_session_id=str(session_record.get("db_session_id", "")).strip()
                or None,
            ),
        )
        context.refresh_from_session_record()
        return context

    @classmethod
    def build(
        cls,
        *,
        session_record: dict[str, Any] | None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        key: str | None = None,
        manifest_path: str | None = None,
    ) -> OrchestrationSessionContext:
        existing = cls.from_session_record(
            session_record,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            key=key,
            manifest_path=manifest_path,
        )
        if existing is not None:
            return existing
        context = cls(
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            session_record=session_record,
            session_record_link=SessionRecordLink(
                key=key,
                manifest_path=manifest_path
                or _resolved_manifest_path(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=session_id,
                ),
            ),
        )
        context.refresh_from_session_record()
        return context

    def refresh_from_session_record(self) -> None:
        self.session_record_link = SessionRecordLink(
            key=self.session_record_link.key
            or self._string_record_value("key")
            or None,
            manifest_path=self.session_record_link.manifest_path
            or _resolved_manifest_path(
                workspace_id=self.workspace_id
                or self._string_record_value("workspace_id"),
                user_id=self.user_id or self._string_record_value("user_id"),
                session_id=self.session_id or self._string_record_value("session_id"),
            ),
            db_session_id=self.session_record_link.db_session_id
            or self._string_record_value("db_session_id")
            or None,
        )
        state = self.load_checkpoint_state()
        self.workflow_stage = state.workflow_stage
        self.pending_approval = state.pending_approval
        self.continuation = state.continuation
        self.continuation_token = (
            state.continuation.continuation_token
            if state.continuation is not None
            else (
                state.pending_approval.continuation_token
                if state.pending_approval is not None
                else None
            )
        )
        self.continuation_metadata = _build_continuation_state_metadata(
            continuation=state.continuation,
            pending_approval=state.pending_approval,
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
        self.workflow_stage = state.workflow_stage
        self.pending_approval = state.pending_approval
        self.continuation = state.continuation
        self.continuation_token = (
            state.continuation.continuation_token
            if state.continuation is not None
            else (
                state.pending_approval.continuation_token
                if state.pending_approval is not None
                else None
            )
        )
        self.continuation_metadata = _build_continuation_state_metadata(
            continuation=state.continuation,
            pending_approval=state.pending_approval,
        )
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

    def _string_record_value(self, key: str) -> str:
        if not isinstance(self.session_record, dict):
            return ""
        return str(self.session_record.get(key, "")).strip()


@dataclass(slots=True)
class SessionSwitchOutcome:
    """Resolved session target, restored state, and outer orchestration context."""

    key: str
    manifest_path: str
    session_record: dict[str, Any]
    last_loaded_docs_path: str | None
    orchestration_session: OrchestrationSessionContext


async def _restore_agent_state(
    *,
    agent: ChatAgentProtocol,
    restored_state: Any,
) -> None:
    if isinstance(restored_state, dict) and restored_state:
        await agent.aimport_session_state(restored_state)
        return
    await agent.areset(clear_sandbox_buffers=True)


def _restorable_session_state(session_record: dict[str, Any]) -> Any:
    session_data = session_record.get("session")
    restored_state: Any = (
        session_data.get("state", {}) if isinstance(session_data, dict) else {}
    )
    manifest_data = session_record.get("manifest")
    if not restored_state and isinstance(manifest_data, dict):
        restored_state = manifest_data.get("state", {})
    return restored_state


def build_orchestration_session_context(
    *,
    session_record: dict[str, Any] | None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    key: str | None = None,
    manifest_path: str | None = None,
) -> OrchestrationSessionContext:
    """Build the authoritative outer orchestration session context."""

    return OrchestrationSessionContext.build(
        session_record=session_record,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        key=key,
        manifest_path=manifest_path,
    )


async def switch_orchestration_session(
    *,
    state: ServerState,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    owner_tenant_claim: str,
    owner_user_claim: str,
    active_key: str | None,
    session_record: dict[str, Any] | None,
    last_loaded_docs_path: str | None,
    local_persist: LocalPersistFn,
) -> SessionSwitchOutcome:
    """Apply websocket session-switch policy under outer orchestration ownership."""

    key = session_key(owner_tenant_claim, owner_user_claim, sess_id)
    owner_id = owner_fingerprint(owner_tenant_claim, owner_user_claim)
    manifest_path = _switch_manifest_path(
        owner_id=owner_id,
        workspace_id=workspace_id,
        session_id=sess_id,
    )

    if active_key == key and session_record is not None:
        return SessionSwitchOutcome(
            key=key,
            manifest_path=manifest_path,
            session_record=session_record,
            last_loaded_docs_path=last_loaded_docs_path,
            orchestration_session=build_orchestration_session_context(
                session_record=session_record,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=sess_id,
                key=key,
                manifest_path=manifest_path,
            ),
        )

    if session_record is not None:
        await local_persist(include_volume_save=True)

    cached: dict[str, Any] | None = state.sessions.get(key)
    if cached is None:
        from ..api.routers.ws.manifest import load_manifest_from_volume

        manifest = (
            await load_manifest_from_volume(agent, manifest_path)
            if interpreter is not None
            else {}
        )
        cached = {
            "key": key,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "owner_tenant_claim": owner_tenant_claim,
            "owner_user_claim": owner_user_claim,
            "owner_fingerprint": owner_id,
            "session_id": sess_id,
            "manifest": manifest if isinstance(manifest, dict) else {},
            "session": {"state": {}, "session_id": sess_id},
        }
        try:
            from fleet_rlm.integrations.local_store import create_session as _db_create

            cached["db_session_id"] = _db_create(title=sess_id).id
        except Exception:
            # Best-effort DB linkage only; continue with in-memory session state if unavailable.
            # Best-effort DB linkage only; continue with in-memory session state if unavailable.
            pass

    cached["session_id"] = sess_id
    cached["workspace_id"] = workspace_id
    cached["user_id"] = user_id
    cached["owner_tenant_claim"] = owner_tenant_claim
    cached["owner_user_claim"] = owner_user_claim
    cached["owner_fingerprint"] = owner_id
    state.sessions[key] = cached

    await _restore_agent_state(
        agent=agent,
        restored_state=_restorable_session_state(cached),
    )

    return SessionSwitchOutcome(
        key=key,
        manifest_path=manifest_path,
        session_record=cached,
        last_loaded_docs_path=None,
        orchestration_session=build_orchestration_session_context(
            session_record=cached,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=sess_id,
            key=key,
            manifest_path=manifest_path,
        ),
    )
