"""Fresh claimed Runs and completed outcomes for lifecycle behavior tests."""

from uuid import uuid4

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from fleet_rlm.sessions.run_state import (
    ClaimedRun,
    _RunClaimToken,
)


def claimed_run() -> ClaimedRun:
    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )


def completed_outcome(*, candidates: tuple[ArtifactCandidate, ...] = (), usage=None) -> RLMOutcome:
    kwargs = {} if usage is None else {"usage": usage}
    return RLMOutcome(
        "completed",
        PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
        artifact_candidates=candidates,
        **kwargs,
    )
