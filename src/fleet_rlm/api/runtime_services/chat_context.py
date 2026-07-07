"""Transport-neutral context for chat turn execution.

Contains ``ChatExecutionContext`` and ``TurnControls`` dataclasses that
decouple the FastAPI transport layer from the DSPy runtime. Both the
WebSocket and SSE transports build a ``ChatExecutionContext`` from their
transport-specific inputs and pass it to ``stream_turn()``.

No import-time side effects. No WebSocket/Request imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime


@dataclass(slots=True)
class TurnControls:
    """Per-turn control fields for chat execution.

    All fields default to ``None`` except ``context_paths`` and
    ``selected_skill_ids`` which default to fresh empty lists via
    ``field(default_factory=list)``.
    """

    execution_mode: str | None = None
    repo_url: str | None = None
    repo_ref: str | None = None
    context_paths: list[str] = field(default_factory=list)
    batch_concurrency: int | None = None
    docs_path: str | None = None
    trace: bool | None = None
    trace_mode: str | None = None
    selected_skill_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ChatExecutionContext:
    """Transport-neutral context for a single chat turn.

    Composes a ``PreparedChatRuntime``, ``NormalizedIdentity``, resolved
    session identifiers, a mutable ``cancel_flag``, and a ``TurnControls``
    sub-object. Both the WebSocket and SSE transports construct this from
    their own transport-specific inputs and pass it to ``stream_turn()``.
    """

    prepared: PreparedChatRuntime
    identity: NormalizedIdentity
    session_id: str | None
    canonical_workspace_id: str | None
    canonical_user_id: str | None
    owner_tenant_claim: str | None
    owner_user_claim: str | None
    cancel_flag: dict[str, bool]
    controls: TurnControls


__all__ = [
    "ChatExecutionContext",
    "TurnControls",
]
