"""Progressive catalog-bound Skill tools."""

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import uuid4

import pytest

from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.tools import SkillToolHost


def test_tools_are_exactly_two_and_resources_require_load() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)
    tools = host.as_tools()
    assert tuple(str(tool.name) for tool in tools) == ("load_skill", "read_skill_resource")
    assert "dictionary" in tools[0].desc
    assert all(field in tools[0].desc for field in ("ok", "skill_markdown", "resources", "error"))
    assert "advertised Skill Card is relevant" in tools[0].desc
    assert "do not load Skills speculatively" in tools[0].desc
    assert "dictionary" in tools[1].desc
    assert all(field in tools[1].desc for field in ("ok", "content", "error"))
    assert "relevant resource" in tools[1].desc
    path = next(iter(skill.resources))
    assert host.read_skill_resource(str(skill.card.id), path) == {"ok": False, "error": "skill_not_loaded"}
    loaded = host.load_skill(str(skill.card.id), skill.card.version)
    assert loaded["ok"] is True
    assert loaded["skill_markdown"] == skill.instructions
    assert all("content" not in resource for resource in loaded["resources"])
    assert (
        host.read_skill_resource(str(skill.card.id), path, skill.card.version)["content"]
        == skill.resources[path].content
    )
    assert host.read_skill_resource(str(skill.card.id), "references/missing.md") == {
        "ok": False,
        "error": "resource_not_found",
    }


def test_load_is_idempotent_and_emits_lifecycle_once() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)

    first = host.load_skill(str(skill.card.id), skill.card.version)
    second = host.load_skill(str(skill.card.id), skill.card.version)

    assert first == second
    assert host.loaded_skill_ids == frozenset({skill.card.id})
    assert [event["kind"] for event in host.drain_public_events()] == ["skill.activated", "skill.loaded"]
    assert host.drain_public_events() == []


def test_load_rejects_closed_identity_and_capacity_errors() -> None:
    catalog = build_bundled_skill_catalog()
    host = SkillToolHost(catalog)
    cards = catalog.cards()

    assert host.load_skill("not-a-uuid") == {"ok": False, "error": "skill_not_found"}
    assert host.load_skill(str(uuid4())) == {"ok": False, "error": "skill_not_found"}
    assert host.load_skill(str(cards[0].id), "0.0.0") == {"ok": False, "error": "version_mismatch"}

    assert all(host.load_skill(str(card.id), card.version)["ok"] is True for card in cards[:4])
    assert host.load_skill(str(cards[4].id), cards[4].version) == {
        "ok": False,
        "error": "skill_limit_exceeded",
    }


def test_preload_restricts_catalog_and_events_are_metadata_only() -> None:
    catalog = build_bundled_skill_catalog()
    selected = catalog.require(stable_skill_id("long-context"))
    other = catalog.require(stable_skill_id("workspace-files"))
    host = SkillToolHost(catalog, allowed_skill_ids=frozenset({selected.card.id}))
    host.mark_preloaded(selected)
    assert host.load_skill(str(other.card.id)) == {"ok": False, "error": "skill_not_found"}
    events = host.drain_public_events()
    assert [event["kind"] for event in events] == ["skill.activated", "skill.loaded"]
    assert selected.instructions not in repr(events)


class _RecordingWorkspace:
    def __init__(self, *, raise_on_write: bool = False) -> None:
        self.written: dict[str, str] = {}
        self.calls: list[str] = []
        self._raise = raise_on_write

    def write_text(self, path: str, content: str, *, overwrite: bool) -> None:
        del overwrite  # overwrite semantics exercised by the host, not the fake
        self.calls.append(path)
        if self._raise:
            raise OSError("volume write failed")
        self.written[path] = content


def test_activated_event_advertises_card_affordances() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)
    host.mark_preloaded(skill)
    events = host.drain_public_events()
    assert events[0]["kind"] == "skill.activated"
    assert events[0]["affordances"] == list(skill.card.affordances)
    assert "affordances" not in events[1]  # loaded lifecycle stays metadata-only


def test_preload_installs_resources_to_session_workspace() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _RecordingWorkspace()
    host = SkillToolHost(catalog, workspace=workspace)

    host.mark_preloaded(skill)

    assert set(workspace.written) == {
        "skills/long-context/scripts/semantic_chunk.py",
        "skills/long-context/scripts/rank_chunks.py",
        "skills/long-context/references/chunking-strategies.md",
    }
    assert workspace.written["skills/long-context/references/chunking-strategies.md"] == (
        skill.resources["references/chunking-strategies.md"].content
    )


def test_progressive_load_reports_installed_paths_and_is_idempotent() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _RecordingWorkspace()
    host = SkillToolHost(catalog, workspace=workspace)

    first = host.load_skill(str(skill.card.id), skill.card.version)
    second = host.load_skill(str(skill.card.id), skill.card.version)

    assert first == second
    assert first["installed_paths"] == [
        "skills/long-context/scripts/semantic_chunk.py",
        "skills/long-context/scripts/rank_chunks.py",
        "skills/long-context/references/chunking-strategies.md",
    ]
    # One write pass for mark + one for the first load (dedupe), none on repeat.
    assert workspace.calls == workspace.calls[: len(workspace.written) * 2]


def test_install_failure_degrades_to_resource_tool_reads_only() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _RecordingWorkspace(raise_on_write=True)
    host = SkillToolHost(catalog, workspace=workspace)

    result = host.load_skill(str(skill.card.id), skill.card.version)

    assert result["ok"] is True
    assert result["installed_paths"] == []
    assert result["resource_install"] == {"declared": 3, "installed": 0, "complete": False}
    assert workspace.written == {}
    # Resources stay reachable through the read tool after a failed install.
    path = next(iter(skill.resources))
    assert host.read_skill_resource(str(skill.card.id), path)["ok"] is True


class _PartialRecordingWorkspace(_RecordingWorkspace):
    def __init__(self, *, fail_after: int) -> None:
        super().__init__()
        self.fail_after = fail_after

    def write_text(self, path: str, content: str, *, overwrite: bool) -> None:
        if len(self.calls) >= self.fail_after:
            self.calls.append(path)
            raise OSError("volume write failed")
        super().write_text(path, content, overwrite=overwrite)


def test_mid_install_failure_reports_exact_successes_and_retries_best_effort() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _PartialRecordingWorkspace(fail_after=1)
    host = SkillToolHost(catalog, workspace=workspace)

    first = host.load_skill(str(skill.card.id), skill.card.version)
    second = host.load_skill(str(skill.card.id), skill.card.version)

    successful_paths = list(workspace.written)
    assert len(successful_paths) == 1
    assert first == second
    assert first["ok"] is True
    assert first["installed_paths"] == successful_paths
    assert first["resource_install"] == {"declared": 3, "installed": 1, "complete": False}
    remaining = "skills/long-context/references/chunking-strategies.md"
    assert remaining not in workspace.written
    resource = host.read_skill_resource(str(skill.card.id), "references/chunking-strategies.md", skill.card.version)
    assert resource["ok"] is True
    assert resource["content"].startswith("# Deterministic chunking helpers")

    assert [event["kind"] for event in host.drain_public_events()] == ["skill.activated", "skill.loaded"]
    assert host.drain_public_events() == []


def test_no_workspace_means_no_install_surface() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)

    first = host.load_skill(str(skill.card.id), skill.card.version)
    second = host.load_skill(str(skill.card.id), skill.card.version)

    assert "installed_paths" not in first
    assert first["resource_install"] == {"declared": 3, "installed": 0, "complete": False}
    assert first == second


@pytest.mark.asyncio
async def test_prepare_host_capabilities_installs_preloaded_skill_resources() -> None:
    from uuid import uuid4 as _uuid4

    from fleet_rlm.chat.capability_preparation import prepare_host_capabilities
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.files.workspace_models import UNAVAILABLE_WORKSPACE_CAPABILITY
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.skills.models import SkillSelectionRef

    async def not_cancelled() -> bool:
        return False

    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    workspace = _RecordingWorkspace()
    turn = ClaimedRun(
        _uuid4(),
        _uuid4(),
        TurnAccess(_uuid4(), _uuid4()),
        TurnInput(
            "analyze the corpus",
            skill_selections=(SkillSelectionRef(id=skill.card.id, expected_version=skill.card.version),),
        ),
        SessionHistory((HistoryMessage("user", "prior"),)),
        not_cancelled,
        _RunClaimToken(_uuid4()),
    )

    spec, _skill_host, notices = await prepare_host_capabilities(
        turn=turn,
        skill_catalog=catalog,
        base_tools=(),
        base_event_views={},
        workspace=UNAVAILABLE_WORKSPACE_CAPABILITY,
        workspace_fs=workspace,
        deadline=float("inf"),
    )

    assert notices == ()
    assert spec.output_schema_id == "fleet.default"  # long-context has no custom signature
    assert "skills/long-context/references/chunking-strategies.md" in workspace.written


def test_tool_event_view_projections_keep_skill_and_resource_bodies_private() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))
    host = SkillToolHost(catalog)
    loaded = host.load_skill(str(skill.card.id), skill.card.version)
    resource_path = next(iter(skill.resources))
    resource = host.read_skill_resource(str(skill.card.id), resource_path, skill.card.version)
    views = host.event_views()

    load_projection = views["load_skill"].output_projection(loaded)
    resource_projection = views["read_skill_resource"].output_projection(resource)

    assert set(load_projection) == {"ok", "skill_id", "name", "version"}
    assert set(resource_projection) == {"ok", "skill_id", "path", "encoding", "media_type", "byte_size"}
    assert "skill_markdown" not in repr(load_projection)
    assert "installed_paths" not in repr(load_projection)
    assert "resources" not in repr(load_projection)
    assert "content" not in repr(resource_projection)
    assert skill.instructions not in repr(load_projection)
    assert resource["content"] not in repr(resource_projection)


def test_lock_is_never_held_across_brokered_workspace_writes() -> None:
    """RC-7 three-way shape: drains proceed while a tool call parks in I/O.

    Live-captured cycle that deadlocked the service loop: a broker Fulfill
    thread held ``SkillToolHost._lock`` across the brokered sandbox
    ``write_text`` posting (which waits on the service loop), while the
    service-loop thread itself synchronously acquired the same lock in
    ``drain_public_events``. Locks now guard book-keeping only, so the drain
    must complete while the write is still parked.
    """
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))

    class _BlockingWorkspace(_RecordingWorkspace):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def write_text(self, path: str, content: str, *, overwrite: bool) -> None:
            self.entered.set()
            assert self.release.wait(timeout=10)
            super().write_text(path, content, overwrite=overwrite)

    workspace = _BlockingWorkspace()
    host = SkillToolHost(catalog, workspace=workspace)
    outcome: dict[str, object] = {}

    def tool_thread() -> None:
        outcome["result"] = host.load_skill(str(skill.card.id), skill.card.version)

    thread = threading.Thread(target=tool_thread, daemon=True)
    thread.start()
    assert workspace.entered.wait(timeout=5), "load_skill did not reach the workspace write"

    # The service-loop-side drain must NOT block on the parked write's lock.
    started = time.perf_counter()
    drained = host.drain_public_events()
    drain_elapsed = time.perf_counter() - started
    assert drain_elapsed < 5, "drain_public_events blocked on the in-flight sandbox write"
    assert drained == []  # lifecycle events publish only after the install settles

    workspace.release.set()
    thread.join(timeout=5)
    assert not thread.is_alive(), "load_skill deadlocked against the concurrent drain"
    assert outcome["result"]["ok"] is True
    assert set(workspace.written) == {f"skills/{skill.card.name}/{path}" for path in skill.resources}

    # Full lifecycle events land once the install settles.
    assert [event["kind"] for event in host.drain_public_events()] == ["skill.activated", "skill.loaded"]


def test_legacy_lock_spanning_write_deadlocks_drains() -> None:
    """Pre-fix reproduction: a lock spanning the write deadlocks the drain.

    Simulates the old mark_preloaded discipline by holding the host's own
    ``_lock`` across the parked workspace write; the concurrent drain on the
    same lock then cannot proceed. Released via the workspace gate so the
    harness exits deterministically.
    """
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("long-context"))

    class _BlockingWorkspace(_RecordingWorkspace):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def write_text(self, path: str, content: str, *, overwrite: bool) -> None:
            self.entered.set()
            assert self.release.wait(timeout=10)
            super().write_text(path, content, overwrite=overwrite)

    workspace = _BlockingWorkspace()
    host = SkillToolHost(catalog, workspace=workspace)
    marker = f"skills/{skill.card.name}/references/chunking-strategies.md"

    def tool_thread() -> None:
        # Legacy discipline: the write happens while holding the host lock.
        host._lock.acquire()
        try:
            workspace.write_text(marker, "payload", overwrite=True)
        finally:
            host._lock.release()

    thread = threading.Thread(target=tool_thread, daemon=True)
    thread.start()
    assert workspace.entered.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        drain: Future[list[dict[str, object]]] = pool.submit(host.drain_public_events)
        with pytest.raises(TimeoutError):
            drain.result(timeout=2)
        workspace.release.set()
        assert drain.result(timeout=5) == [], "cancellation released the legacy-shape drain"

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert workspace.written[marker] == "payload"
