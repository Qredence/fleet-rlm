"""Session persistence interface owned by the Session domain."""

from __future__ import annotations

from typing import Any, Protocol

from fleet_rlm_clean.artifacts.models import ArtifactCandidate
from fleet_rlm_clean.sessions.checkpoints import TurnClaim


class SessionRepository(Protocol):
    """Interface consumed by chat and HTTP modules."""

    async def load(self, session_id: Any) -> Any: ...

    async def create(self, **kwargs: Any) -> Any: ...

    async def list(self, **kwargs: Any) -> Any: ...

    async def get_owned(self, session_id: Any, **kwargs: Any) -> Any: ...

    async def turn_count(self, session_id: Any) -> int: ...

    async def update(self, session_id: Any, **kwargs: Any) -> Any: ...

    async def archive(self, session_id: Any, **kwargs: Any) -> Any: ...

    async def list_turns(self, session_id: Any, **kwargs: Any) -> Any: ...

    async def request_cancel(self, run_id: Any, **kwargs: Any) -> Any: ...

    async def claim_turn(
        self,
        session_id: Any,
        *,
        idempotency_key: str | None = None,
        run_id: Any | None = None,
    ) -> TurnClaim: ...

    async def commit_completed_turn(
        self,
        session_id: Any,
        *,
        user_text: str,
        assistant_text: str,
        run_id: Any | None = None,
        expected_checkpoint_version: int | None = None,
        artifact_candidates: tuple[ArtifactCandidate, ...] = (),
    ) -> Any: ...

    async def finish_failed_run(self, session_id: Any, run_id: Any, *, message: str | None = None) -> Any: ...

    async def is_cancel_requested(self, run_id: Any) -> bool: ...
