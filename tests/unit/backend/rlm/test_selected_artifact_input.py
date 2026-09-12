"""Selected Artifact reads use the prepared Turn owner, never child storage authority."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.artifacts.errors import ArtifactNotFoundError
from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
from fleet_rlm.artifacts.reader import ArtifactReader, StoredArtifact
from fleet_rlm.chat.capability_preparation import prepare_host_capabilities
from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
from fleet_rlm.rlm.recursion import RecursiveRLMOptions, SubproblemCapsule
from fleet_rlm.sessions.models import SessionHistory, TurnInput
from fleet_rlm.skills.catalog import build_bundled_skill_catalog
from fleet_rlm.workspace.models import UNAVAILABLE_WORKSPACE_CAPABILITY
from tests.unit.backend.rlm.fakes import EmptyCapabilities
from tests.unit.backend.rlm.test_recursion_policy_surface import _context, _RecordingFactory


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "revoked", "missing", "invalid_uri"])
async def test_prepared_artifact_is_read_on_application_loop_with_turn_scope_and_exact_allowance(
    case: str,
) -> None:
    artifact_id = uuid4()
    if case == "invalid_uri":
        with pytest.raises(ValueError, match="UUID"):
            SubproblemCapsule(task="Read selected evidence", authorized_references=("artifact://not-a-uuid",))
        return
    locator = f"artifact://{artifact_id}"
    capsule = SubproblemCapsule(task="Read selected evidence", authorized_references=(locator,))
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "delegate", "code": f"outcome = rlm_query(capsule={capsule.model_dump(mode='json')!r})"},
            {"reasoning": "read", "code": "text = read_selected_input(evidence_id='reference-1')"},
            {"reasoning": "child submit", "code": "SUBMIT(answer=text + ' [reference-1]')"},
            {
                "reasoning": "root submit",
                "code": "assert outcome['source_references'] == ['reference-1']; SUBMIT(answer=outcome['answer'])",
            },
        ],
        adapter=dspy.JSONAdapter(),
    )
    factory = _RecordingFactory()
    context, runner = _context(
        root=root,
        sub=dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        factory=factory,
        recursive_options=RecursiveRLMOptions(enabled=True),
    )
    data = "selected évidence".encode()
    ref = ArtifactRef(artifact_id, uuid4(), uuid4(), "text", None, "text/plain", len(data), sha256(data).hexdigest())
    loop = asyncio.get_running_loop()
    reads: list[object] = []

    class Catalog:
        async def get(self, *, access, artifact_id):
            assert asyncio.get_running_loop() is loop
            assert access == ArtifactAccess(context.identity.access.user_id, context.identity.access.workspace_id)
            assert artifact_id == ref.id
            reads.append("authorized")
            if case == "missing":
                raise ArtifactNotFoundError("Artifact not found")
            return StoredArtifact(ref, "private/committed-content")

    class Blobs:
        async def read_bytes(self, workspace_id, logical_path):
            assert workspace_id == context.identity.access.workspace_id
            assert logical_path == "private/committed-content"
            reads.append("content")
            if case == "revoked":
                context.identity.authority.revoke()
                await asyncio.sleep(0)
            return data

    class RecordingReader(ArtifactReader):
        async def content(self, access, artifact_id, *, max_bytes=None):
            assert max_bytes == capsule.allocation_bytes - capsule.serialized_bytes
            return await super().content(access, artifact_id, max_bytes=max_bytes)

    async def not_cancelled():
        return False

    turn = ClaimedRun(
        context.identity.run_id,
        context.identity.session_id,
        context.identity.access,
        TurnInput("delegate"),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    spec, _, _ = await prepare_host_capabilities(
        turn=turn,
        skill_catalog=build_bundled_skill_catalog(),
        base_tools=(),
        base_event_views={},
        workspace=UNAVAILABLE_WORKSPACE_CAPABILITY,
        artifact_reader=RecordingReader(catalog=Catalog(), blobs=Blobs()),
        deadline=context.execution.deadline,
    )
    context = replace(context, capabilities=EmptyCapabilities(spec=spec))
    stream = runner.stream(context)
    _ = [event async for event in stream]
    assert stream.outcome is not None
    assert stream.outcome.succeeded is (case == "valid")
    if case == "valid":
        assert stream.outcome.prediction.display_text == "selected évidence [reference-1]"
    expected_reads = {
        "valid": ["authorized", "content"],
        "revoked": ["authorized", "content"],
        "missing": ["authorized"],
        "invalid_uri": [],
    }
    assert reads == expected_reads[case]
    assert factory.close_counts == {1: 1}
