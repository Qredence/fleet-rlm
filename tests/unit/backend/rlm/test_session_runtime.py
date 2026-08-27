"""P45 Session-scoped Root RLM runtime core tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import dspy
import pytest

from fleet_rlm.rlm.session_runtime import (
    RuntimeHealth,
    RuntimeUnavailableError,
    SessionKey,
    SessionRLMRegistry,
    SessionRLMState,
    compute_program_fingerprint,
)


class FakeInterpreter:
    """Caller-owned interpreter double with persistent ordinary state."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {}
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class FakeCleanup:
    """Provider cleanup double used to prove idempotent injected cleanup."""

    def __init__(self) -> None:
        self.calls: list[SessionRLMState] = []

    async def __call__(self, state: SessionRLMState) -> None:
        self.calls.append(state)


class FakeCleanupHandle:
    """Closeable state-owned provider handle."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def _components(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "dspy_version": "3.3.1",
        "signature_fields": {
            "request": {"type": "str", "required": True},
            "answer": {"type": "str", "required": True},
        },
        "signature_instructions": "Answer the request.",
        "root_lm_config": {"model": "openai/root", "base_url": "https://root.example"},
        "sub_lm_config": {"model": "openai/sub", "base_url": "https://sub.example"},
        "tools": [
            {
                "name": "lookup",
                "description": "Look up a value.",
                "schema": {"type": "object", "properties": {"key": {"type": "string"}}},
            }
        ],
        "recursion_policy": {"root_depth": 0, "child_depth": 1, "max_children": 2},
        "limits": {"max_iters": 4, "max_llm_calls": 8, "max_output_chars": 1024},
        "output_contract": {"answer": {"type": "string", "required": True}},
        "skill_signature": {"name": "DataAnalysisSignature", "fields": ["request", "answer"]},
        "skill_instructions": "Use bounded tabular analysis.",
        "interpreter_protocol_version": "fleet-interpreter-v1",
    }
    values.update(overrides)
    return values


def _make_factory(created: list[SessionRLMState] | None = None):
    built = created if created is not None else []

    async def factory(key: SessionKey, fingerprint: str) -> SessionRLMState:
        state = SessionRLMState(
            session_key=key,
            program_fingerprint=fingerprint,
            # The production field is the native DSPy RLM.  The core does not
            # construct it, so unit tests can use a tiny fake object here.
            rlm=object(),
            interpreter=FakeInterpreter(),
        )
        built.append(state)
        return state

    return factory, built


@pytest.mark.asyncio
async def test_same_key_and_fingerprint_reuses_native_objects_and_clean_interpreter_state() -> None:
    factory, built = _make_factory()
    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace-a", "session-a")
    fingerprint = str(compute_program_fingerprint(_components()))

    first = await registry.acquire(key, fingerprint)
    assert isinstance(first, SessionRLMState)
    first.interpreter.namespace["ordinary_value"] = 42  # type: ignore[attr-defined]
    second = await registry.acquire(key, fingerprint)

    assert second is first
    assert second.rlm is first.rlm
    assert second.interpreter is first.interpreter
    assert second.interpreter.namespace["ordinary_value"] == 42  # type: ignore[attr-defined]
    assert len(built) == 1

    # Workspace is part of the tenancy key, not merely Session UUID text.
    other_workspace = await registry.acquire(SessionKey("workspace-b", "session-a"), fingerprint)
    assert other_workspace is not first
    assert len(built) == 2


@pytest.mark.asyncio
async def test_same_key_creation_is_serialized() -> None:
    entered = asyncio.Event()
    proceed = asyncio.Event()
    active = 0
    max_active = 0
    built: list[SessionRLMState] = []

    async def factory(key: SessionKey, fingerprint: str) -> SessionRLMState:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        entered.set()
        await proceed.wait()
        active -= 1
        state = SessionRLMState(key, fingerprint, object(), FakeInterpreter())
        built.append(state)
        return state

    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")
    first_task = asyncio.create_task(registry.acquire(key, "fp"))
    await entered.wait()
    second_task = asyncio.create_task(registry.acquire(key, "fp"))
    await asyncio.sleep(0)
    assert not second_task.done()
    assert max_active == 1

    proceed.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert first is second
    assert len(built) == 1


@pytest.mark.asyncio
async def test_fingerprint_mismatch_rotates_and_closes_old_state() -> None:
    factory, _built = _make_factory()
    cleanup = FakeCleanup()
    registry = SessionRLMRegistry(factory, cleanup=cleanup)
    key = SessionKey("workspace", "session")

    old = await registry.acquire(key, "fingerprint-a")
    old_interpreter = old.interpreter
    fresh = await registry.acquire(key, "fingerprint-b")

    assert fresh is not old
    assert old.closed
    assert old.health is RuntimeHealth.CLOSED
    assert old_interpreter.close_calls == 1  # type: ignore[attr-defined]
    assert cleanup.calls == [old]
    assert fresh.generation == old.generation + 1

    await registry.close(fresh)
    assert fresh.closed
    assert fresh.interpreter.close_calls == 1  # type: ignore[attr-defined]
    assert cleanup.calls == [old, fresh]


@pytest.mark.asyncio
async def test_fingerprint_rotation_handoffs_root_owned_interpreter_before_close() -> None:
    shared_interpreter = FakeInterpreter()
    shared_root = FakeCleanupHandle()
    built: list[SessionRLMState] = []

    async def factory(key: SessionKey, fingerprint: str) -> SessionRLMState:
        state = SessionRLMState(
            key,
            fingerprint,
            object(),
            shared_interpreter,
            root_lease=shared_root,
            interpreter_owned_by_root=True,
        )
        built.append(state)
        return state

    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")
    first = await registry.acquire_execution(key, "fingerprint-a", context_binding="none")
    first.mark_committed()
    await first.release()
    old = first.state

    second = await registry.acquire_execution(
        key,
        "fingerprint-b",
        context_binding="none",
        preserve_interpreter=shared_interpreter,
    )

    assert second.state is built[1]
    assert second.state.interpreter is shared_interpreter
    assert old.closed
    assert shared_interpreter.close_calls == 0
    assert second.state.root_lease is shared_root

    second.mark_committed()
    await second.release()
    await registry.close(key)
    assert shared_root.close_calls == 1
    assert shared_interpreter.close_calls == 0


@pytest.mark.asyncio
async def test_configured_idle_eviction_is_noop_without_policy() -> None:
    factory, _built = _make_factory()
    registry = SessionRLMRegistry(factory)
    assert await registry.evict_configured_idle() == ()


@pytest.mark.asyncio
async def test_failed_runtime_close_is_removed_before_next_generation() -> None:
    factory, built = _make_factory()

    cleanup_calls = 0

    async def failing_cleanup(_state: SessionRLMState) -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise RuntimeError("cleanup failed")

    registry = SessionRLMRegistry(factory, cleanup=failing_cleanup)
    key = SessionKey("workspace", "session")
    old = await registry.acquire(key, "fp")

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await registry.close(key)

    assert old.closed
    assert registry.get(key) is None
    fresh = await registry.acquire(key, "fp")
    assert fresh is not old
    assert len(built) == 2
    await registry.shutdown()


@pytest.mark.asyncio
async def test_bounded_shutdown_quarantines_stuck_active_state_and_drains_later() -> None:
    factory, _built = _make_factory()
    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")
    lease = await registry.acquire_execution(key, "fp")
    state = lease.state

    await registry.shutdown(drain_seconds=0)

    assert registry.get(key) is None
    assert state.tainted
    assert not state.closed
    deferred = tuple(registry._deferred_close_tasks)
    assert deferred

    lease.mark_tainted()
    await lease.release()
    await asyncio.wait_for(asyncio.gather(*deferred), timeout=1)
    assert state.closed


@pytest.mark.asyncio
async def test_state_close_waiter_cancellation_keeps_owned_close_task() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingCleanup:
        async def aclose(self) -> None:
            entered.set()
            await release.wait()

    state = SessionRLMState(
        SessionKey("workspace", "session"),
        "fp",
        object(),
        FakeInterpreter(),
        cleanup_handle=BlockingCleanup(),
    )
    owner = asyncio.create_task(state.aclose())
    await entered.wait()

    waiter = asyncio.create_task(state.aclose())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert state._close_task is not None
    assert not state._close_task.done()
    release.set()
    await owner
    assert state.closed


@pytest.mark.asyncio
async def test_shutdown_reports_inflight_factory_as_deferred_ownership() -> None:
    entered = asyncio.Event()
    proceed = asyncio.Event()

    async def factory(key: SessionKey, fingerprint: str) -> SessionRLMState:
        entered.set()
        await proceed.wait()
        return SessionRLMState(key, fingerprint, object(), FakeInterpreter())

    registry = SessionRLMRegistry(factory)
    acquisition = asyncio.create_task(registry.acquire(SessionKey("workspace", "session"), "fp"))
    await entered.wait()

    await registry.shutdown(drain_seconds=0)
    assert registry.has_deferred_closes
    assert await registry.wait_deferred_closes(timeout=0) is False

    proceed.set()
    with pytest.raises(RuntimeUnavailableError, match="shut down"):
        await acquisition
    assert await registry.wait_deferred_closes(timeout=1) is True


@pytest.mark.asyncio
async def test_close_unhealthy_retires_tainted_state_before_next_preparation() -> None:
    factory, built = _make_factory()
    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")
    state = await registry.acquire(key, "fp")
    registry.mark_tainted(state)

    assert await registry.close_unhealthy(key) is True
    assert state.closed
    assert registry.get(key) is None
    assert await registry.close_unhealthy(key) is False
    assert len(built) == 1


@pytest.mark.asyncio
async def test_commit_settlement_cannot_resurrect_tainted_lease() -> None:
    factory, _built = _make_factory()
    registry = SessionRLMRegistry(factory)
    lease = await registry.acquire_execution(SessionKey("workspace", "session"), "fp")
    lease.mark_tainted()
    with pytest.raises(RuntimeUnavailableError, match="already tainted"):
        lease.mark_committed()
    await lease.release()
    await registry.shutdown()


@pytest.mark.asyncio
async def test_tainted_generation_cannot_taint_replacement() -> None:
    factory, built = _make_factory()
    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")
    stale = await registry.acquire_execution(key, "fp")
    await stale.release()
    fresh = await registry.acquire_execution(key, "fp")
    # The stale lease is already released and must not affect the new state.
    stale.mark_tainted()
    assert fresh.state is not stale.state
    assert fresh.state.healthy
    fresh.mark_committed()
    await fresh.release()
    assert len(built) == 2
    await registry.shutdown()


@pytest.mark.asyncio
async def test_tainted_state_is_never_returned() -> None:
    factory, built = _make_factory()
    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")

    old = await registry.acquire(key, "same-fingerprint")
    registry.mark_tainted(old)
    assert old.tainted
    assert old.health is RuntimeHealth.TAINTED

    fresh = await registry.acquire(key, "same-fingerprint")
    assert fresh is not old
    assert old.closed
    assert len(built) == 2


@pytest.mark.asyncio
async def test_execution_lock_serializes_same_session_but_not_different_sessions() -> None:
    factory, _ = _make_factory()
    registry = SessionRLMRegistry(factory)
    same_key = SessionKey("workspace", "same")
    other_key = SessionKey("workspace", "other")
    same_a = await registry.acquire(same_key, "fp")
    same_b = await registry.acquire(same_key, "fp")
    other = await registry.acquire(other_key, "fp")
    entered: list[str] = []
    release_first = asyncio.Event()

    async def run(state: SessionRLMState, label: str) -> None:
        async with registry.execution(state):
            entered.append(label)
            if label == "same-a":
                await release_first.wait()

    first = asyncio.create_task(run(same_a, "same-a"))
    await asyncio.sleep(0)
    second = asyncio.create_task(run(same_b, "same-b"))
    different = asyncio.create_task(run(other, "other"))
    await asyncio.sleep(0)

    assert entered == ["same-a", "other"]
    assert same_a.execution_lock.locked()
    assert not other.execution_lock.locked()

    release_first.set()
    await asyncio.gather(first, second, different)
    assert entered == ["same-a", "other", "same-b"]


@pytest.mark.asyncio
async def test_idle_eviction_waits_for_inactive_state_then_closes_it() -> None:
    factory, built = _make_factory()
    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")
    state = await registry.acquire(key, "fp")
    old = datetime.now(UTC) - timedelta(minutes=5)
    state.last_used_at = old
    entered = asyncio.Event()
    unblock = asyncio.Event()

    async def active_turn() -> None:
        async with registry.execution(state):
            state.last_used_at = old
            entered.set()
            await unblock.wait()

    turn = asyncio.create_task(active_turn())
    await entered.wait()
    eviction = asyncio.create_task(
        registry.evict_idle(idle_seconds=1, now=datetime.now(UTC)),
    )
    await asyncio.sleep(0)
    assert not eviction.done()
    assert state.draining
    assert not state.closed

    unblock.set()
    await turn
    assert await eviction == (key,)
    assert state.closed
    assert state.interpreter.close_calls == 1  # type: ignore[attr-defined]
    assert registry.get(key) is None
    assert len(built) == 1


@pytest.mark.asyncio
async def test_session_delete_shutdown_and_cleanup_are_idempotent() -> None:
    factory, built = _make_factory()
    cleanup = FakeCleanup()
    registry = SessionRLMRegistry(factory, cleanup=cleanup)
    key = SessionKey("workspace", "session")

    deleted = await registry.acquire(key, "fp")
    await registry.delete_session(key)
    await registry.delete_session(key)
    assert deleted.closed
    assert deleted.interpreter.close_calls == 1  # type: ignore[attr-defined]
    assert cleanup.calls == [deleted]

    resident = await registry.acquire(key, "fp")
    await registry.shutdown()
    await registry.shutdown()
    assert resident.closed
    assert resident.interpreter.close_calls == 1  # type: ignore[attr-defined]
    assert cleanup.calls == [deleted, resident]
    assert len(built) == 2
    with pytest.raises(RuntimeUnavailableError, match="shut down"):
        await registry.acquire(key, "fp")


@pytest.mark.asyncio
async def test_runtime_cleanup_handle_is_awaitable_and_state_close_is_idempotent() -> None:
    cleanup_handle = FakeCleanupHandle()
    key = SessionKey("workspace", "session")
    state = SessionRLMState(key, "fp", object(), FakeInterpreter(), cleanup_handle=cleanup_handle)

    await state.aclose()
    await state.aclose()

    assert cleanup_handle.close_calls == 1
    assert state.closed
    assert state.health is RuntimeHealth.CLOSED
    assert state.interpreter.close_calls == 1  # type: ignore[attr-defined]


def test_signature_field_descriptions_and_defaults_change_runner_fingerprint_shape() -> None:
    from fleet_rlm.rlm.runner import _signature_shape

    class First(dspy.Signature):
        request: str = dspy.InputField(desc="First request", default="one")
        answer: str = dspy.OutputField()

    class Second(dspy.Signature):
        request: str = dspy.InputField(desc="Second request", default="two")
        answer: str = dspy.OutputField()

    assert _signature_shape(First) != _signature_shape(Second)


def test_signature_field_order_changes_runner_fingerprint_shape() -> None:
    from fleet_rlm.rlm.runner import _signature_shape

    class First(dspy.Signature):
        request: str = dspy.InputField()
        context: str = dspy.InputField()
        answer: str = dspy.OutputField()

    class Second(dspy.Signature):
        context: str = dspy.InputField()
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    class Third(dspy.Signature):
        request: list[str] = dspy.InputField()
        answer: str = dspy.OutputField()

    class Fourth(dspy.Signature):
        request: list[int] = dspy.InputField()
        answer: str = dspy.OutputField()

    assert _signature_shape(First) != _signature_shape(Second)
    assert _signature_shape(Third) != _signature_shape(Fourth)


@pytest.mark.parametrize(
    "changed",
    [
        {"signature_fields": {"request": {"type": "str"}, "answer": {"type": "int"}}},
        {"signature_instructions": "Return JSON."},
        {"root_lm_config": {"model": "different/root"}},
        {"sub_lm_config": {"model": "different/sub"}},
        {"tools": [{"name": "lookup", "description": "changed", "schema": {}}]},
        {"recursion_policy": {"root_depth": 0, "child_depth": 2}},
        {"limits": {"max_iters": 5, "max_llm_calls": 8, "max_output_chars": 1024}},
        {"output_contract": {"answer": {"type": "int"}}},
        {"skill_signature": {"name": "OtherSkill"}},
        {"skill_instructions": "Use a different skill."},
        {"interpreter_protocol_version": "fleet-interpreter-v2"},
    ],
)
def test_required_program_compatibility_inputs_change_digest(changed: dict[str, object]) -> None:
    baseline = compute_program_fingerprint(_components())
    modified = compute_program_fingerprint(_components(**changed))
    assert modified != baseline


def test_program_fingerprint_rejects_mismatched_dspy_version() -> None:
    with pytest.raises(ValueError, match="installed version"):
        compute_program_fingerprint(_components(dspy_version="3.3.0"))


def test_runner_lm_shape_tracks_open_ended_policy_without_content_or_secrets() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.runner import _lm_shape

    first = SimpleNamespace(
        model="openai/root",
        provider=SimpleNamespace(),
        kwargs={
            "temperature": 0.2,
            "seed": 1,
            "api_key": "secret-a",
            "prompt": "private request a",
            "base_url": "https://user:secret-a@root.example/v1?token=a",
        },
    )
    second = SimpleNamespace(
        model="openai/root",
        provider=SimpleNamespace(),
        kwargs={
            "temperature": 0.2,
            "seed": 2,
            "api_key": "secret-b",
            "prompt": "private request b",
            "base_url": "https://user:secret-b@root.example/v1?token=b",
        },
    )

    first_shape = _lm_shape(first)
    second_shape = _lm_shape(second)
    assert first_shape != second_shape
    assert first_shape["seed"] == 1
    assert first_shape["base_url"] == "https://root.example/v1"
    assert "api_key" not in first_shape
    assert "prompt" not in first_shape
    assert "secret-a" not in str(first_shape)
    assert "secret-b" not in str(second_shape)


def test_fingerprint_omits_unknown_lm_values_and_secret_tool_text() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.runner import _lm_shape, _tool_shape

    lm_a = SimpleNamespace(model="openai/root", kwargs={"custom_value": "secret-a"})
    lm_b = SimpleNamespace(model="openai/root", kwargs={"custom_value": "secret-with-a-different-length"})
    assert _lm_shape(lm_a) == _lm_shape(lm_b)

    def read(value: str) -> str:
        return value

    tool_a = dspy.Tool(read, name="read", desc="token=secret-a")
    tool_b = dspy.Tool(read, name="read", desc="token=secret-with-a-different-length")
    assert _tool_shape(tool_a) == _tool_shape(tool_b)


def test_requests_history_attachments_memory_and_secrets_do_not_change_digest() -> None:
    baseline = compute_program_fingerprint(
        _components(
            request="first request",
            history=[{"request": "old", "answer": "old"}],
            attachment_ids=["attachment-a"],
            memory={"fact": "first"},
            api_key="secret-a",
            access_token="token-a",
            root_lm_config={
                "model": "openai/root",
                "api_key": "secret-a",
                "base_url": "https://user:secret-a@root.example/v1?api_key=secret-a",
            },
        )
    )
    changed_excluded = compute_program_fingerprint(
        _components(
            request="second request",
            history=[{"request": "different", "answer": "different"}],
            attachment_ids=["attachment-b"],
            memory={"fact": "second"},
            api_key="secret-b",
            access_token="token-b",
            root_lm_config={
                "model": "openai/root",
                "api_key": "secret-b",
                "base_url": "https://user:secret-b@root.example/v1?api_key=secret-b",
            },
        )
    )
    assert changed_excluded == baseline
    assert "secret-a" not in str(baseline)
    assert "secret-b" not in str(changed_excluded)


def test_exact_installed_dspy_type_can_be_stored_without_registry_construction() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm

    rlm = build_native_rlm(
        signature="request -> answer",
        options=RLMOptions(max_iters=1, max_llm_calls=1, max_output_chars=128),
        verbose=False,
    )
    assert type(rlm) is dspy.RLM
    state = SessionRLMState(SessionKey("workspace", "session"), "fp", rlm, FakeInterpreter())
    assert state.rlm is rlm


@pytest.mark.asyncio
async def test_execution_lease_holds_lane_until_commit_and_cleans_stale_binding() -> None:
    factory, built = _make_factory()
    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")

    first = await registry.acquire_execution(key, "fp", context_binding="none")
    assert first.state.active
    assert first.state.execution_lock.locked()
    first.mark_committed()
    await first.release()
    assert not first.state.active
    assert not first.state.execution_lock.locked()

    second = await registry.acquire_execution(key, "fp", context_binding="none")
    assert second.state is first.state
    await second.release()  # unsettled release taints the resident fail-closed
    assert first.state.tainted
    rotated = await registry.acquire(key, "fp")
    assert rotated is not first.state
    assert len(built) == 2
    await registry.shutdown()


@pytest.mark.asyncio
async def test_context_binding_change_rotates_without_changing_program_fingerprint() -> None:
    factory, _built = _make_factory()
    registry = SessionRLMRegistry(factory)
    key = SessionKey("workspace", "session")

    first = await registry.acquire_execution(key, "fp", context_binding="manifest-a")
    first.mark_committed()
    await first.release()
    second = await registry.acquire_execution(key, "fp", context_binding="manifest-b")
    assert second.state is not first.state
    assert second.state.program_fingerprint == "fp"
    await second.release()
    await registry.shutdown()


def test_observability_events_are_bounded_and_secret_free() -> None:
    events: list[tuple[str, dict[str, str]]] = []
    registry = SessionRLMRegistry(event_sink=lambda name, payload: events.append((name, dict(payload))))
    assert registry.observability_snapshot() == {}


def test_fingerprint_canonicalization_shapes_nested_unknown_and_free_text_values() -> None:
    """Secrets in open-ended metadata never become canonical fingerprint inputs."""
    from fleet_rlm.rlm.session_runtime import _canonical_components

    def canonical(secret: str) -> object:
        return _canonical_components(
            {
                "root_lm_config": {"custom_value": secret},
                "signature_fields": {"answer": {"default": secret}},
                "signature_instructions": f"Use key {secret}",
                "tools": [{"name": "lookup", "description": f"description {secret}"}],
            }
        )

    first = canonical("super-secret-a")
    second = canonical("super-secret-b")
    encoded_first = json.dumps(first, sort_keys=True)
    encoded_second = json.dumps(second, sort_keys=True)

    assert "super-secret-a" not in encoded_first
    assert "super-secret-b" not in encoded_second
    assert first == second

    # Runner-side projections are canonical inputs too; they must not retain
    # the same values before the shared component boundary runs.
    from fleet_rlm.rlm.runner import _field_shape, _signature_shape, _tool_schema_shape, _tool_shape

    class SecretSignature(dspy.Signature):
        request: str = dspy.InputField(default="super-secret-a")
        answer: str = dspy.OutputField()

    SecretSignature.instructions = "Use super-secret-a when answering."
    assert "super-secret-a" not in str(_signature_shape(SecretSignature))
    assert "super-secret-a" not in str(_field_shape(SecretSignature.model_fields["request"]))

    def lookup(value: str) -> str:
        return value

    secret_tool = dspy.Tool(lookup, name="lookup", desc="description super-secret-a")
    assert "super-secret-a" not in str(_tool_shape(secret_tool))
    secret_schema = _tool_schema_shape({"type": "integer", "default": 123456, "enum": ["super-secret-a"]})
    assert "123456" not in str(secret_schema)
    assert "super-secret-a" not in str(secret_schema)

    assert compute_program_fingerprint(
        {"root_lm_config": {"custom_value": "super-secret-a"}}
    ) == compute_program_fingerprint({"root_lm_config": {"custom_value": "super-secret-b"}})


def test_fingerprint_shape_keeps_public_model_limits_schema_type_and_field_order() -> None:
    """Shape-only boundaries do not erase documented compatibility inputs."""
    model_a = compute_program_fingerprint({"root_lm_config": {"model": "provider/model-a"}})
    model_b = compute_program_fingerprint({"root_lm_config": {"model": "provider/model-b"}})
    limits_a = compute_program_fingerprint({"limits": {"max_iters": 2}})
    limits_b = compute_program_fingerprint({"limits": {"max_iters": 3}})
    schema_a = compute_program_fingerprint({"signature_fields": {"answer": {"type": "str"}}})
    schema_b = compute_program_fingerprint({"signature_fields": {"answer": {"type": "int"}}})
    order_a = compute_program_fingerprint(
        {
            "signature_fields": {
                "request": {"type": "str"},
                "answer": {"type": "str"},
            }
        }
    )
    order_b = compute_program_fingerprint(
        {
            "signature_fields": {
                "answer": {"type": "str"},
                "request": {"type": "str"},
            }
        }
    )

    assert model_a != model_b
    assert limits_a != limits_b
    assert schema_a != schema_b
    assert order_a != order_b


def test_runner_tool_shape_preserves_public_argument_schema_type() -> None:
    """The outer Tool argument-name mapping does not erase JSON-schema types."""
    from fleet_rlm.rlm.runner import _tool_shape

    def lookup(value: str) -> str:
        return value

    shape = _tool_shape(dspy.Tool(lookup, name="lookup", desc="Read a value."))

    assert shape["args"]["value"]["type"] == "string"
