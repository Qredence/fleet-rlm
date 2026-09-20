"""Regression: artifact workspace-publish notices must not break detail draining."""

from __future__ import annotations

import pytest

from fleet_rlm.chat.preparation import PreparedHostCapabilities


class _FilesStub:
    def __init__(self, events):
        self._events = events

    def drain_public_events(self):
        events, self._events = self._events, []
        return events


class _SkillsStub:
    def drain_public_events(self):
        return []


def test_drain_public_details_skips_artifact_workspace_publish_notices() -> None:
    """``artifact.workspace_publish`` notices carry ``path`` (no attachment_id).

    RC-1's kwargs fix made artifact tools callable, which exposed this latent
    drain crash: ``drain_public_details`` previously read ``item["attachment_id"]``
    for every pending file event and died with ``KeyError`` on publish notices.
    """
    files = _FilesStub(
        [
            {
                "event_kind": "artifact.workspace_publish",
                "path": "collatz.md",
                "kind": "markdown",
                "title": "collatz.md",
                "byte_size": 9233,
            },
            {
                "event_kind": "attachment.read",
                "attachment_id": "3c7b6c75-f805-4f27-a637-bc012e6a0213",
                "filename": "notes.txt",
                "byte_size": 8000,
            },
        ]
    )
    prepared = PreparedHostCapabilities(
        spec=None,
        files=files,
        skills=_SkillsStub(),
        close_files=False,
        artifact_candidates=False,
    )

    details = prepared.drain_public_details()

    assert len(details) == 1
    assert details[0].filename == "notes.txt"
    assert details[0].byte_size == 8000


@pytest.mark.asyncio
async def test_aclose_closes_files_and_artifacts_independently_and_reraises_first_error() -> None:
    class Files:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            raise ValueError("files failed")

    class Artifacts:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError("artifacts failed")

    files = Files()
    artifacts = Artifacts()
    prepared = PreparedHostCapabilities(
        spec=None,
        files=files,
        skills=_SkillsStub(),
        close_files=True,
        artifact_candidates=True,
        artifacts=artifacts,
    )

    with pytest.raises(ValueError, match="files failed"):
        await prepared.aclose()

    assert files.closed is True
    assert artifacts.closed is True


@pytest.mark.asyncio
async def test_aclose_closes_artifacts_when_file_ownership_is_not_declared() -> None:
    class Artifacts:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    artifacts = Artifacts()
    prepared = PreparedHostCapabilities(
        spec=None,
        files=object(),
        skills=_SkillsStub(),
        close_files=False,
        artifact_candidates=True,
        artifacts=artifacts,
    )

    await prepared.aclose()

    assert artifacts.closed is True
