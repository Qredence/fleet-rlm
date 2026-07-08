"""Tests for stream_turn's TurnControls threading and execution_mode dispatch.

Split from the former monolithic ``test_stream_turn.py`` during Phase 2A.2
test/contract cleanup. Covers:

  VAL-REF-009, VAL-REF-010

plus the internal ``_build_stream_kwargs`` helper that both validations rely
on. See ``test_stream_turn_legacy_backend.py``,
``test_stream_turn_execution_backend.py``, and ``test_stream_turn_errors.py``
for the remaining stream_turn coverage, and ``conftest.py`` / ``fakes.py`` in
this directory for shared fixtures.
"""

from __future__ import annotations

import pytest

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from fleet_rlm.api.runtime_services.stream_turn import _build_stream_kwargs, stream_turn
from tests.unit.runtime_services.fakes import StubAgent

# ---------------------------------------------------------------------------
# VAL-REF-009 — stream_turn threads TurnControls fields to runtime
# ---------------------------------------------------------------------------


class TestThreadsTurnControls:
    """VAL-REF-009: Supported TurnControls fields thread into legacy runtime kwargs."""

    @pytest.mark.asyncio
    async def test_all_fields_threaded_when_set(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Supported non-None TurnControls fields appear in kwargs."""
        controls = TurnControls(
            execution_mode="rlm",
            repo_url="https://example.com/repo.git",
            repo_ref="main",
            context_paths=["src/"],
            batch_concurrency=3,
            docs_path="./docs",
            trace=True,
            trace_mode="full",
            selected_skill_ids=["skill-a", "skill-b"],
        )
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(  # type: ignore[arg-type]
                tenant_claim="t", user_claim="u", email="t@t.com"
            ),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=controls,
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, StubAgent)

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

        assert agent.captured_kwargs is not None
        # execution_mode is handled by set_execution_mode, not kwargs.
        for key in (
            "repo_url",
            "repo_ref",
            "context_paths",
            "batch_concurrency",
            "docs_path",
            "trace",
        ):
            expected = getattr(controls, key)
            if expected is not None:
                assert key in agent.captured_kwargs, (
                    f"Expected {key}={expected!r} in kwargs, got {agent.captured_kwargs}"
                )
                actual = agent.captured_kwargs[key]
                if isinstance(expected, list):
                    assert list(actual) == expected
                else:
                    assert actual == expected
        assert controls.trace_mode == "full"
        assert controls.selected_skill_ids == ["skill-a", "skill-b"]
        assert "trace_mode" not in agent.captured_kwargs
        assert agent.captured_kwargs["selected_skill_ids"] == ["skill-a", "skill-b"]

    @pytest.mark.asyncio
    async def test_none_fields_not_forwarded(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
    ) -> None:
        """None/empty fields are not forwarded as non-None."""
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert stub_agent.captured_kwargs is not None
        # TurnControls fields that are None should not be in kwargs.
        # (execution_mode is handled via set_execution_mode)
        assert "trace" not in stub_agent.captured_kwargs or stub_agent.captured_kwargs["trace"] is None
        # context_paths default is [] — empty so not forwarded.
        assert "context_paths" not in stub_agent.captured_kwargs
        # selected_skill_ids default is [] — empty so not forwarded.
        assert "selected_skill_ids" not in stub_agent.captured_kwargs

    @pytest.mark.asyncio
    async def test_context_paths_copy_not_mutated(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
    ) -> None:
        """context_paths is copied (list()) so original isn't mutated."""
        sample_context.controls.context_paths = ["src/", "lib/"]
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert stub_agent.captured_kwargs is not None
        assert stub_agent.captured_kwargs["context_paths"] == ["src/", "lib/"]

        # Mutating the original doesn't affect kwargs.
        sample_context.controls.context_paths.append("extra/")
        assert stub_agent.captured_kwargs["context_paths"] == ["src/", "lib/"]

    @pytest.mark.asyncio
    async def test_context_only_controls_not_forwarded_to_legacy_runtime(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
    ) -> None:
        """trace_mode remains context-only; selected_skill_ids forward when set."""
        sample_context.controls.trace_mode = "full"
        sample_context.controls.selected_skill_ids = ["skill-a"]
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert stub_agent.captured_kwargs is not None
        assert sample_context.controls.trace_mode == "full"
        assert sample_context.controls.selected_skill_ids == ["skill-a"]
        assert "trace_mode" not in stub_agent.captured_kwargs
        assert stub_agent.captured_kwargs["selected_skill_ids"] == ["skill-a"]


# ---------------------------------------------------------------------------
# VAL-REF-010 — stream_turn sets execution_mode from TurnControls
# ---------------------------------------------------------------------------


class TestSetsExecutionMode:
    """VAL-REF-010: set_execution_mode called when controls.execution_mode
    is not None; not called when None."""

    @pytest.mark.asyncio
    async def test_sets_execution_mode_when_not_none(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """set_execution_mode called with the value when execution_mode is set."""
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_mode="simple"),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, StubAgent)

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

        assert agent.set_execution_mode_calls == ["simple"]
        assert agent.execution_mode == "simple"

    @pytest.mark.asyncio
    async def test_no_set_execution_mode_when_none(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
    ) -> None:
        """set_execution_mode not called when execution_mode is None."""
        assert sample_context.controls.execution_mode is None

        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert stub_agent.set_execution_mode_calls == []


# ---------------------------------------------------------------------------
# Helper: _build_stream_kwargs unit test
# ---------------------------------------------------------------------------


class TestBuildStreamKwargs:
    """Unit tests for the internal _build_stream_kwargs helper."""

    def test_includes_message_and_cancel_check(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """kwargs always includes message and cancel_check."""
        kwargs = _build_stream_kwargs(sample_context, "test-msg")
        assert kwargs["message"] == "test-msg"
        assert callable(kwargs["cancel_check"])

    def test_cancel_check_returns_false_by_default(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """cancel_check returns False when cancel_flag is not set."""
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["cancel_check"]() is False

    def test_cancel_check_returns_true_when_set(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """cancel_check returns True when cancel_flag['cancelled'] is True."""
        sample_context.cancel_flag["cancelled"] = True
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["cancel_check"]() is True

    def test_trace_field(self, sample_context: ChatExecutionContext) -> None:
        """trace is in kwargs when set."""
        sample_context.controls.trace = False
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["trace"] is False

    def test_docs_path_field(self, sample_context: ChatExecutionContext) -> None:
        """docs_path is in kwargs when set."""
        sample_context.controls.docs_path = "./docs"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["docs_path"] == "./docs"

    def test_repo_url_field(self, sample_context: ChatExecutionContext) -> None:
        """repo_url is in kwargs when set."""
        sample_context.controls.repo_url = "https://example.com/repo"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["repo_url"] == "https://example.com/repo"

    def test_repo_ref_field(self, sample_context: ChatExecutionContext) -> None:
        """repo_ref is in kwargs when set."""
        sample_context.controls.repo_ref = "main"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["repo_ref"] == "main"

    def test_context_paths_field(self, sample_context: ChatExecutionContext) -> None:
        """context_paths is in kwargs when non-empty."""
        sample_context.controls.context_paths = ["src/"]
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["context_paths"] == ["src/"]

    def test_context_paths_empty_not_in_kwargs(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Empty context_paths is not forwarded."""
        sample_context.controls.context_paths = []
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert "context_paths" not in kwargs

    def test_batch_concurrency_field(self, sample_context: ChatExecutionContext) -> None:
        """batch_concurrency is in kwargs when set."""
        sample_context.controls.batch_concurrency = 3
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["batch_concurrency"] == 3

    def test_trace_mode_field_stays_context_only(self, sample_context: ChatExecutionContext) -> None:
        """trace_mode remains on controls but is not a legacy runtime kwarg."""
        sample_context.controls.trace_mode = "full"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert sample_context.controls.trace_mode == "full"
        assert "trace_mode" not in kwargs

    def test_selected_skill_ids_forwarded_when_set(self, sample_context: ChatExecutionContext) -> None:
        """Non-empty selected_skill_ids are forwarded to legacy runtime kwargs."""
        sample_context.controls.selected_skill_ids = ["skill-a"]
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert sample_context.controls.selected_skill_ids == ["skill-a"]
        assert kwargs["selected_skill_ids"] == ["skill-a"]

    def test_selected_skill_ids_empty_not_in_kwargs(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Empty selected_skill_ids is not forwarded."""
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert "selected_skill_ids" not in kwargs
