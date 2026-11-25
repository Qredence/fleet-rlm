"""Integration checks for run admission and Neon Postgres 18 persistence."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from fleet_rlm.config import Settings
from fleet_rlm.db import (
    Memory,
    Run,
    Session,
    SessionFactory,
    SessionNotFoundError,
    TurnConflictError,
    WorkspaceSettings,
    acquire_root_run,
    clear_retired_sandbox,
    create_child_run,
    create_db,
    create_session,
    finish_run,
    get_session_or_raise,
    get_session_prior_turns,
    get_session_ui_messages,
    get_turn_for_refinement,
    get_workspace_settings_overrides,
    list_approved_session_memories,
    list_deleting_sessions,
    list_discarded_object_batch,
    list_discarded_objects,
    list_retired_sandboxes,
    list_session_files,
    reap_stale_runs,
    rebind_session_sandbox,
    recall_procedures,
    register_object_write,
    release_upload,
    reserve_session_sandbox,
    reset_workspace_settings_overrides,
    save_or_update_memory,
    save_turn_lesson,
    search_memories,
    update_workspace_settings_overrides,
    upsert_file_record,
)


def _base_settings_kwargs() -> dict[str, object]:
    return {
        "_env_file": None,
        "database_url": "postgresql://localhost/unused",
        "database_direct_url": "postgresql://localhost/unused",
        "daytona_api_key": "offline",
        "daytona_image": "offline",
        "llm_model": "openai/gpt-4o-mini",
        "sub_llm_model": "openai/gpt-4o-mini",
        "llm_api_key": "offline",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_concurrent_turns_per_worker": 0},
        {"max_concurrent_turns_per_worker": 257},
        {"stale_run_timeout_seconds": 29},
        {"stale_run_timeout_seconds": 86_401},
        {"daytona_idle_timeout_minutes": 0},
        {"daytona_idle_timeout_minutes": 1441},
        {"daytona_exec_timeout_seconds": 4},
        {"daytona_exec_timeout_seconds": 601},
        {"rlm_max_iters": 0},
        {"rlm_max_iters": 101},
        {"rlm_max_llm_calls": 0},
        {"rlm_max_llm_calls": 501},
        {"rlm_max_output_chars": 499},
        {"rlm_max_output_chars": 200_001},
        {"rlm_max_depth": 0},
        {"rlm_max_depth": 8},
        {"history_max_turns": -1},
        {"history_max_turns": 101},
        {"history_max_chars": 999},
        {"history_max_chars": 200_001},
    ],
)
def test_settings_rejects_out_of_bounds_values(overrides: dict[str, object]) -> None:
    kwargs = _base_settings_kwargs()
    kwargs.update(overrides)
    with pytest.raises(ValidationError):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_settings_accepts_boundary_values() -> None:
    kwargs = _base_settings_kwargs()
    kwargs.update(
        {
            "max_concurrent_turns_per_worker": 1,
            "stale_run_timeout_seconds": 30,
            "daytona_idle_timeout_minutes": 1,
            "daytona_exec_timeout_seconds": 5,
            "rlm_max_iters": 1,
            "rlm_max_llm_calls": 1,
            "rlm_max_output_chars": 500,
            "rlm_max_depth": 7,
            "history_max_turns": 0,
            "history_max_chars": 1_000,
        }
    )
    settings = Settings(**kwargs)  # type: ignore[arg-type]
    assert settings.rlm_max_depth == 7
    assert settings.history_max_turns == 0


@pytest_asyncio.fixture
async def db() -> AsyncIterator[SessionFactory]:
    if not Path(".env").is_file() and "FLEET_DATABASE_URL" not in os.environ:
        pytest.skip("Configure backend/.env with a migrated Neon Postgres 18 branch")

    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError:
        pytest.skip("Configure backend/.env with a migrated Neon Postgres 18 branch")
    if (urlparse(settings.database_url).hostname or "").startswith("ep-example"):
        pytest.skip("Configure backend/.env with a real Neon Postgres 18 branch URL")

    engine, factory = create_db(settings)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_root_lock_blocks_only_competing_roots(db: SessionFactory) -> None:
    session = await create_session(db, title="Phase 1 locking test")
    try:
        async with db.begin() as connection:
            uploading = await connection.get_one(Session, session.id)
            uploading.upload_key = "sessions/abandoned-upload"
            uploading.upload_started_at = datetime.now(UTC)
        with pytest.raises(TurnConflictError, match="upload in progress"):
            await acquire_root_run(db, session.id, "During upload")
        async with db.begin() as connection:
            uploading = await connection.get_one(Session, session.id)
            uploading.upload_started_at = datetime.now(UTC) - timedelta(minutes=10)

        contenders = await asyncio.gather(
            acquire_root_run(db, session.id, "First contender"),
            acquire_root_run(db, session.id, "Second contender"),
            return_exceptions=True,
        )
        winners = [result for result in contenders if isinstance(result, Run)]
        conflicts = [result for result in contenders if isinstance(result, TurnConflictError)]
        assert len(winners) == len(conflicts) == 1, contenders
        cause = conflicts[0].__cause__
        assert isinstance(cause, IntegrityError)
        assert (
            getattr(getattr(cause.orig, "diag", None), "constraint_name", None)
            == "uq_runs_one_running_root_per_session"
        )

        root = winners[0]
        assert (await get_session_or_raise(db, session.id)).upload_key is None
        assert root.id.version == 7
        assert root.root_run_id == root.id
        assert root.status == "running"

        children = await asyncio.gather(
            create_child_run(db, root.id, kind="subagent", prompt="Delegate"),
            create_child_run(db, root.id, kind="llm_query", prompt="Ask"),
        )
        assert len({child.id for child in children}) == 2
        for child in children:
            assert child.session_id == session.id
            assert child.parent_run_id == child.root_run_id == root.id
            assert child.depth == 1
            assert child.status == "running"
        with pytest.raises(TurnConflictError):
            await acquire_root_run(db, session.id, "Still locked")
        for child in children:
            assert await finish_run(db, child.id, status="completed")

        history = {"messages": [{"prompt": root.prompt, "answer": "Done"}]}
        parts = [
            {
                "type": "data-turn-details",
                "data": {
                    "runtime": "dspy.RLM",
                    "root_actions": 2,
                    "child_rlms": 1,
                    "sub_lm_queries": 3,
                    "completion": "submit",
                    "trace_url": "https://mlflow.test/",
                },
            },
            {"type": "text", "text": "Done"},
        ]
        assert await finish_run(
            db, root.id, status="completed", answer="Done", ui_parts=parts, history_json=history
        )
        assert (await get_session_or_raise(db, session.id)).history_json == history
        assert await get_session_ui_messages(db, session.id) == [
            {
                "id": f"{root.id}:user",
                "role": "user",
                "parts": [{"type": "text", "text": root.prompt}],
            },
            {"id": str(root.id), "role": "assistant", "parts": parts},
        ]
        (prior,) = await get_session_prior_turns(db, session.id, max_turns=1, max_chars=100)
        assert prior["assistant"] == "Done"
        assert prior["runtime"] == (
            "dspy.RLM, 2 root actions, 1 child RLMs started, "
            "3 sub-LM queries launched. Completion: submit."
        )
        next_root = await acquire_root_run(db, session.id, "Next turn")
        assert next_root.id != root.id
        assert next_root.status == "running"
    finally:
        async with db.begin() as connection:
            await connection.execute(delete(Session).where(Session.id == session.id))


@pytest.mark.asyncio
async def test_reap_stale_runs_session_scoped_and_global(db: SessionFactory) -> None:
    session_a = await create_session(db, title="Reap test A")
    session_b = await create_session(db, title="Reap test B")
    try:
        root_a = await acquire_root_run(db, session_a.id, "Stale root A")
        vm_a = f"vm-{uuid4().hex}"
        await rebind_session_sandbox(db, session_a.id, vm_a, owner_run_id=root_a.id)
        child_a = await create_child_run(db, root_a.id, kind="subagent", prompt="Fresh child A")
        grandchild_a = await create_child_run(
            db, child_a.id, kind="llm_query", prompt="Fresh grandchild A"
        )

        root_b = await acquire_root_run(db, session_b.id, "Stale root B")
        child_b = await create_child_run(db, root_b.id, kind="subagent", prompt="Fresh child B")

        # Backdate only the roots; their fresh descendants must still be reaped alongside them.
        async with db.begin() as connection:
            await connection.execute(
                update(Run)
                .where(Run.id.in_([root_a.id, root_b.id]))
                .values(created_at=func.now() - timedelta(seconds=600))
            )

        reaped_a = await reap_stale_runs(db, 300, session_id=session_a.id)
        assert reaped_a == 3
        retiring = await get_session_or_raise(db, session_a.id)
        assert retiring.daytona_sandbox_id == vm_a and retiring.sandbox_needs_cleanup
        with pytest.raises(TurnConflictError, match="active turn"):
            await rebind_session_sandbox(
                db, session_a.id, f"vm-{uuid4().hex}", expected=vm_a, owner_run_id=root_a.id
            )

        async with db() as connection:
            runs = {
                row.id: row
                for row in (
                    await connection.scalars(
                        select(Run).where(
                            Run.id.in_(
                                [root_a.id, child_a.id, grandchild_a.id, root_b.id, child_b.id]
                            )
                        )
                    )
                ).all()
            }

        for run_id in (root_a.id, child_a.id, grandchild_a.id):
            assert runs[run_id].status == "failed"
            assert runs[run_id].error == "Run expired"
            assert runs[run_id].finished_at is not None

        assert runs[root_b.id].status == "running"
        assert runs[child_b.id].status == "running"

        # Session A's lock is now free; verify a fresh root survives while its stale child reaps.
        fresh_root_a = await acquire_root_run(db, session_a.id, "Fresh root A")
        new_vm = f"vm-{uuid4().hex}"
        assert await rebind_session_sandbox(
            db, session_a.id, new_vm, expected=vm_a, owner_run_id=fresh_root_a.id
        ) == (new_vm, True)
        assert not (await get_session_or_raise(db, session_a.id)).sandbox_needs_cleanup
        stale_child_a = await create_child_run(
            db, fresh_root_a.id, kind="llm_query", prompt="Stale child under fresh root"
        )
        async with db.begin() as connection:
            await connection.execute(
                update(Run)
                .where(Run.id == stale_child_a.id)
                .values(created_at=func.now() - timedelta(seconds=600))
            )

        reaped_child = await reap_stale_runs(db, 300, session_id=session_a.id)
        assert reaped_child == 1

        reaped_global = await reap_stale_runs(db, 300)
        assert reaped_global >= 2

        async with db() as connection:
            after = {
                row.id: row
                for row in (
                    await connection.scalars(
                        select(Run).where(
                            Run.id.in_([root_b.id, child_b.id, fresh_root_a.id, stale_child_a.id])
                        )
                    )
                ).all()
            }

        assert after[stale_child_a.id].status == "failed"
        assert after[root_b.id].status == "failed"
        assert after[child_b.id].status == "failed"
        assert after[fresh_root_a.id].status == "running"
    finally:
        async with db.begin() as connection:
            await connection.execute(
                delete(Session).where(Session.id.in_([session_a.id, session_b.id]))
            )


@pytest.mark.asyncio
async def test_rebind_session_sandbox(db: SessionFactory) -> None:
    session_single = await create_session(db, title="Sandbox single bind")
    session_race = await create_session(db, title="Sandbox race bind")
    try:
        with pytest.raises(SessionNotFoundError):
            await rebind_session_sandbox(db, uuid4(), f"sb-missing-{uuid4().hex}")

        single_id = f"sb-single-{uuid4().hex}"
        bound_id, won = await rebind_session_sandbox(db, session_single.id, single_id)
        assert (bound_id, won) == (single_id, True)
        assert (await get_session_or_raise(db, session_single.id)).daytona_sandbox_id == single_id

        original = await acquire_root_run(db, session_single.id, "Reserve replacement")
        name, recovering = await reserve_session_sandbox(
            db, session_single.id, original.id, expected=single_id
        )
        assert not recovering
        finished = await finish_run(db, original.id, status="failed", retire_sandbox_id=single_id)
        assert finished
        fenced = await get_session_or_raise(db, session_single.id)
        assert fenced.sandbox_needs_cleanup and fenced.pending_sandbox_name == name
        retry = await acquire_root_run(db, session_single.id, "Recover replacement")
        resumed = await reserve_session_sandbox(db, session_single.id, retry.id, expected=single_id)
        assert resumed == (name, True)
        rebound = await rebind_session_sandbox(
            db,
            session_single.id,
            "test-recovered-" + retry.id.hex,
            expected=single_id,
            owner_run_id=retry.id,
            allocation_name=name,
        )
        assert rebound[1]
        active = await get_session_or_raise(db, session_single.id)
        assert not active.sandbox_needs_cleanup and active.pending_sandbox_name is None

        sb_a = f"sb-race-a-{uuid4().hex}"
        sb_b = f"sb-race-b-{uuid4().hex}"
        results = await asyncio.gather(
            rebind_session_sandbox(db, session_race.id, sb_a),
            rebind_session_sandbox(db, session_race.id, sb_b),
        )
        winners = [item for item in results if item[1]]
        losers = [item for item in results if not item[1]]
        assert len(winners) == len(losers) == 1
        winning_id = winners[0][0]
        assert winning_id in {sb_a, sb_b}
        assert losers[0] == (winning_id, False)
        assert (await get_session_or_raise(db, session_race.id)).daytona_sandbox_id == winning_id

        follow_up_id, follow_up_won = await rebind_session_sandbox(
            db, session_race.id, f"sb-race-c-{uuid4().hex}"
        )
        assert (follow_up_id, follow_up_won) == (winning_id, False)
        replacement_id = f"sb-replacement-{uuid4().hex}"
        assert await rebind_session_sandbox(
            db, session_race.id, replacement_id, expected=winning_id
        ) == (replacement_id, True)
        assert await rebind_session_sandbox(db, session_race.id, None, expected=winning_id) == (
            replacement_id,
            False,
        )
    finally:
        async with db.begin() as connection:
            await connection.execute(
                delete(Session).where(Session.id.in_([session_single.id, session_race.id]))
            )


@pytest.mark.asyncio
async def test_sweep_queries_skip_active_turns_and_upload_leases(db: SessionFactory) -> None:
    idle = await create_session(db, title="Sweep idle")
    busy = await create_session(db, title="Sweep busy")
    try:
        idle_vm, busy_vm = f"vm-{uuid4().hex}", f"vm-{uuid4().hex}"
        finished = await acquire_root_run(db, idle.id, "Retire the sandbox")
        await rebind_session_sandbox(db, idle.id, idle_vm, owner_run_id=finished.id)
        orphan = f"sessions/{idle.id}/{uuid4().hex}"
        await register_object_write(db, idle.id, orphan, "multipart-orphan", run_id=finished.id)
        assert await finish_run(db, finished.id, status="failed", retire_sandbox_id=idle_vm)

        active = await acquire_root_run(db, busy.id, "Still running")
        await rebind_session_sandbox(db, busy.id, busy_vm, owner_run_id=active.id)
        staged = f"sessions/{busy.id}/{uuid4().hex}"
        await register_object_write(db, busy.id, staged, "multipart-staged", run_id=active.id)
        upload = f"sessions/{busy.id}/{uuid4().hex}"
        async with db.begin() as connection:
            await connection.execute(
                update(Session)
                .where(Session.id == busy.id)
                .values(sandbox_needs_cleanup=True, upload_key=upload, upload_started_at=func.now())
            )
        await register_object_write(db, busy.id, upload, "multipart-upload")

        retired = await list_retired_sandboxes(db, after=None, limit=1_000)
        assert (idle.id, idle_vm) in retired and busy.id not in {sid for sid, _ in retired}
        assert idle.id not in {
            sid for sid, _ in await list_retired_sandboxes(db, after=idle.id, limit=1_000)
        }

        async def discarded(after: str) -> set[str]:
            batch = await list_discarded_object_batch(db, after=after, limit=1_000)
            return {entry.object_key for entry in batch}

        assert orphan in await discarded(f"sessions/{idle.id}/")
        assert orphan not in await discarded(orphan)
        assert not {staged, upload} & await discarded(f"sessions/{busy.id}/")
        await release_upload(db, busy.id, upload)
        assert upload in await discarded(f"sessions/{busy.id}/")

        assert not await clear_retired_sandbox(db, busy.id, busy_vm)
        assert not await clear_retired_sandbox(db, idle.id, f"vm-{uuid4().hex}")
        assert await clear_retired_sandbox(db, idle.id, idle_vm)
        cleared = await get_session_or_raise(db, idle.id)
        assert cleared.daytona_sandbox_id is None and not cleared.sandbox_needs_cleanup
        assert not await clear_retired_sandbox(db, idle.id, idle_vm)

        async with db.begin() as connection:
            await connection.execute(
                update(Session).where(Session.id == busy.id).values(deleting=True)
            )
        assert busy.id in {
            item.id for item in await list_deleting_sessions(db, after=None, limit=1_000)
        }
        assert busy.id not in {
            item.id for item in await list_deleting_sessions(db, after=busy.id, limit=1_000)
        }
    finally:
        async with db.begin() as connection:
            await connection.execute(delete(Session).where(Session.id.in_([idle.id, busy.id])))


@pytest.mark.asyncio
async def test_upsert_file_record_and_list_session_files(db: SessionFactory) -> None:
    session = await create_session(db, title="File records test")
    try:
        input_file = await upsert_file_record(
            db,
            session.id,
            path="/workspace/inputs/data.csv",
            sha256="1" * 64,
            size_bytes=128,
            kind="input",
            object_key="inputs/v1",
        )
        artifact_v1 = await upsert_file_record(
            db,
            session.id,
            path="/workspace/artifacts/report.md",
            sha256="2" * 64,
            size_bytes=256,
            kind="artifact",
            object_key="artifacts/v1",
        )

        initial = await list_session_files(db, session.id)
        assert [f.path for f in initial] == [
            "/workspace/artifacts/report.md",
            "/workspace/inputs/data.csv",
        ]
        assert initial[0].sha256 == "2" * 64
        assert initial[0].size_bytes == 256
        assert initial[1].id == input_file.id

        artifact_v2 = await upsert_file_record(
            db,
            session.id,
            path="/workspace/artifacts/report.md",
            sha256="3" * 64,
            size_bytes=512,
            kind="artifact",
            object_key="artifacts/v2",
        )
        assert artifact_v2.id == artifact_v1.id
        assert artifact_v2.sha256 == "3" * 64
        assert artifact_v2.size_bytes == 512

        updated = await list_session_files(db, session.id)
        assert len(updated) == 2
        assert updated[0].path == "/workspace/artifacts/report.md"
        assert updated[0].sha256 == "3" * 64
        assert updated[0].size_bytes == 512
        assert updated[0].object_key == "artifacts/v2"
        garbage = await list_discarded_objects(db, session.id)
        assert [entry.object_key for entry in garbage] == ["artifacts/v1"]

        for path in (
            "/workspace/artifacts/report.md/appendix.md",
            "/workspace/artifacts/report.md",
        ):
            run = await acquire_root_run(db, session.id, "Replace the artifact path")
            key = f"artifacts/{run.id}"
            await register_object_write(db, session.id, key, "multipart-fixture", run_id=run.id)
            assert key not in {
                row.object_key for row in await list_discarded_objects(db, session.id)
            }
            finished = await finish_run(
                db,
                run.id,
                status="completed",
                files=[
                    {
                        "path": path,
                        "kind": "artifact",
                        "sha256": "4" * 64,
                        "size_bytes": 32,
                        "object_key": key,
                    },
                ],
            )
            assert finished
            assert key not in {
                row.object_key for row in await list_discarded_objects(db, session.id)
            }
            assert [f.path for f in await list_session_files(db, session.id)] == [
                path,
                input_file.path,
            ]
    finally:
        async with db.begin() as connection:
            await connection.execute(delete(Session).where(Session.id == session.id))


@pytest.mark.asyncio
async def test_search_memories_and_save_or_update_memory(db: SessionFactory) -> None:
    session_a = await create_session(db, title="Memory scope A")
    session_b = await create_session(db, title="Memory scope B")
    global_ids: list[UUID] = []
    token = f"nebula{uuid4().hex[:8]}"
    calibration_token = f"calibration{uuid4().hex[:8]}"
    try:
        global_approved = await save_or_update_memory(
            db,
            session_id=None,
            content=f"Global {token} guideline for deterministic outputs",
            approved=True,
        )
        global_ids.append(global_approved.id)

        global_draft = await save_or_update_memory(
            db,
            session_id=None,
            content=f"Global {token} unapproved draft note",
            approved=False,
        )
        global_ids.append(global_draft.id)

        session_a_mem = await save_or_update_memory(
            db,
            session_id=session_a.id,
            content=f"Session A {token} {calibration_token} result",
            approved=True,
        )
        session_b_mem = await save_or_update_memory(
            db,
            session_id=session_b.id,
            content=f"Session B {token} private metric",
            approved=True,
        )
        await save_or_update_memory(
            db, session_id=session_a.id, content="Pending calibration proposal", approved=False
        )

        global_hits = await search_memories(db, None, token)
        assert [m.id for m in global_hits] == [global_approved.id]

        global_hits_all = await search_memories(db, None, token, include_unapproved=True)
        assert {m.id for m in global_hits_all} == {global_approved.id, global_draft.id}

        session_a_hits = await search_memories(db, session_a.id, token)
        assert {m.id for m in session_a_hits} == {global_approved.id, session_a_mem.id}
        assert session_b_mem.id not in {m.id for m in session_a_hits}

        # Multi-word conversational prompt with extra words still matches via relaxed OR fallback.
        conversational_hits = await search_memories(
            db, session_a.id, f"{token} unmatched{uuid4().hex[:8]}"
        )
        assert {m.id for m in conversational_hits} == {global_approved.id, session_a_mem.id}
        assert session_b_mem.id not in {m.id for m in conversational_hits}

        # Fixture-specific terms keep both overlap cases independent of existing global memories.
        unmatched_terms = f"samples{uuid4().hex[:8]} analysis{uuid4().hex[:8]}"
        incidental_prompt = f"{token} {unmatched_terms}"
        assert await search_memories(db, session_a.id, incidental_prompt) == []
        topical_prompt = f"{token} {calibration_token} {unmatched_terms}"
        topical_hits = await search_memories(db, session_a.id, topical_prompt)
        assert [m.id for m in topical_hits] == [session_a_mem.id]

        # Ordinary prompts without lexical matches should not receive arbitrary memories.
        assert await search_memories(db, session_a.id, f"unmatched{uuid4().hex[:8]}") == []
        synthesis_memories = await list_approved_session_memories(db, session_a.id)
        assert [memory.id for memory in synthesis_memories] == [session_a_mem.id]

        updated_session_a = await save_or_update_memory(
            db,
            session_id=session_a.id,
            content=f"Session A {token} revised calibration schema",
            approved=True,
            memory_id=session_a_mem.id,
        )
        assert updated_session_a.id == session_a_mem.id
        assert "revised calibration schema" in updated_session_a.content

        promoted_global = await save_or_update_memory(
            db,
            session_id=None,
            content=f"Global {token} approved note",
            approved=True,
            memory_id=global_draft.id,
        )
        assert promoted_global.id == global_draft.id
        assert promoted_global.approved is True

        global_hits_after = await search_memories(db, None, token)
        assert {m.id for m in global_hits_after} == {global_approved.id, global_draft.id}

        with pytest.raises(LookupError):
            await save_or_update_memory(
                db,
                session_id=session_b.id,
                content="Wrong scope update",
                approved=True,
                memory_id=session_a_mem.id,
            )
    finally:
        async with db.begin() as connection:
            if global_ids:
                await connection.execute(delete(Memory).where(Memory.id.in_(global_ids)))
            await connection.execute(
                delete(Session).where(Session.id.in_([session_a.id, session_b.id]))
            )


@pytest.mark.asyncio
async def test_procedures_require_approval_and_stay_in_their_source_session(
    db: SessionFactory,
) -> None:
    session = await create_session(db, title="Procedure recall")
    try:
        typed = await acquire_root_run(db, session.id, "Compare web citations")
        assert await finish_run(db, typed.id, status="completed")
        generic = await acquire_root_run(db, session.id, "Verify migration revisions")
        assert await finish_run(db, generic.id, status="completed")
        other_id = uuid4()
        assert await get_turn_for_refinement(db, other_id, typed.id) is None

        targeted, duplicate = await asyncio.gather(
            *(
                save_turn_lesson(
                    db,
                    session.id,
                    typed.id,
                    content="Narrow the source after a truncated extract.",
                    skill_name="web-research",
                )
                for _ in range(2)
            )
        )
        assert targeted.id == duplicate.id and targeted.approved is False
        source = await get_turn_for_refinement(db, session.id, typed.id)
        assert source is not None and source[1] is not None and source[1].id == targeted.id
        general = await save_turn_lesson(
            db,
            session.id,
            generic.id,
            content="Verify migration revisions before deploying.",
            skill_name=None,
        )
        query = "Verify migration revisions"
        assert await recall_procedures(db, session.id, query, ["web-research"]) == []
        async with db.begin() as connection:
            await connection.execute(
                update(Memory).where(Memory.session_id == session.id).values(approved=True)
            )
        recalled = await recall_procedures(db, session.id, query, ["web-research"])
        assert [memory.id for memory in recalled] == [targeted.id, general.id]
        recalled = await recall_procedures(db, session.id, query, ["spreadsheet-audit"])
        assert [memory.id for memory in recalled] == [general.id]
        assert await recall_procedures(db, other_id, query, ["web-research"]) == []

        facts = await search_memories(db, session.id, query, include_unapproved=True)
        assert general.id not in {memory.id for memory in facts}
        with pytest.raises(LookupError):
            await save_or_update_memory(
                db, session_id=session.id, content="Attempted fact update", memory_id=targeted.id
            )
        async with db.begin() as connection:
            await connection.execute(
                update(Memory).where(Memory.id == targeted.id).values(approved=False)
            )
        assert await recall_procedures(db, session.id, "Unrelated task", ["web-research"]) == []
    finally:
        async with db.begin() as connection:
            await connection.execute(delete(Session).where(Session.id == session.id))


@pytest.mark.asyncio
async def test_workspace_settings_overrides_lifecycle(db: SessionFactory) -> None:
    async with db() as connection:
        existing = await connection.get(WorkspaceSettings, 1)
        prior_exists = existing is not None
        prior_overrides = dict(existing.overrides_json) if existing is not None else None
    try:
        assert await reset_workspace_settings_overrides(db) == {}
        assert await get_workspace_settings_overrides(db) == {}

        merged = await update_workspace_settings_overrides(
            db, {"rlm_max_iters": 12, "daytona_target": "us"}
        )
        assert merged == {"rlm_max_iters": 12, "daytona_target": "us"}
        assert await get_workspace_settings_overrides(db) == merged

        patched = await update_workspace_settings_overrides(
            db, {"rlm_max_depth": 5, "daytona_target": None}
        )
        assert patched == {"rlm_max_iters": 12, "rlm_max_depth": 5}
        assert await get_workspace_settings_overrides(db) == patched

        assert await reset_workspace_settings_overrides(db) == {}
        assert await get_workspace_settings_overrides(db) == {}
    finally:
        async with db.begin() as connection:
            if not prior_exists:
                await connection.execute(delete(WorkspaceSettings).where(WorkspaceSettings.id == 1))
            else:
                await connection.execute(
                    insert(WorkspaceSettings)
                    .values(id=1, overrides_json=prior_overrides or {})
                    .on_conflict_do_update(
                        index_elements=[WorkspaceSettings.id],
                        set_={"overrides_json": prior_overrides or {}, "updated_at": func.now()},
                    )
                )
