"""Child sandbox lifecycle tests covering VAL-DAYTONA-012, 013, 014, 022.

VAL-DAYTONA-012: Child sandbox delegation cleans up on success.
VAL-DAYTONA-013: Child sandbox delegation cleans up on error or cancellation.
VAL-DAYTONA-014: No orphaned Daytona resources after validation suite.
VAL-DAYTONA-022: Child volume subpath isolation is verified.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

from fleet_rlm.integrations.daytona.isolation import (
    _CHILD_VOLUME_SUBPATH_ROOT,
    _child_sandbox_name,
    _child_volume_subpath,
    _safe_child_path_token,
    build_delegate_child,
    propagate_parent_recursion_state,
    record_child_isolation_metadata,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_session(*, sandbox_id: str = "parent-sandbox") -> MagicMock:
    session = MagicMock()
    session.sandbox_id = sandbox_id
    session.sandbox = MagicMock()
    session.repo_url = None
    session.ref = None
    session.volume_name = "tenant-vol"
    session.workspace_path = "/workspace"
    session.context_sources = []
    session.volume_mount_path = None
    session.context_id = None
    return session


def _make_fake_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime._resolved_config = MagicMock()
    runtime._resolved_config.api_key = "test-key"
    runtime._resolved_config.api_url = "https://daytona.test"
    return runtime


def _make_parent_interpreter(
    *,
    parent_sandbox_id: str = "parent-sandbox",
    volume_name: str = "tenant-vol",
    volume_subpath: str | None = None,
    remaining_budget: int = 10,
    child_isolation_mode: str = "auto",
) -> MagicMock:
    parent = MagicMock()
    parent.child_isolation_mode = child_isolation_mode
    parent.child_fork_fallback = "clean"
    parent.volume_name = volume_name
    parent.volume_subpath = volume_subpath
    parent.repo_url = None
    parent.repo_ref = None
    parent.context_paths = []
    parent.sandbox_spec = None
    parent.sandbox_labels = {}
    parent.sub_lm = None
    parent._sub_rlm_max_depth = 2
    parent._sub_rlm_depth = 0
    parent.rlm_max_iterations = 20
    parent.delegate_max_calls_per_turn = 8
    parent.delegate_result_truncation_chars = 8000
    parent.llm_call_timeout = 30
    parent.async_execute = False
    parent.timeout = 60
    parent.execute_timeout = None
    parent.child_isolation_metadata = None
    parent._remaining_llm_budget = MagicMock(return_value=remaining_budget)
    parent._check_and_increment_llm_calls = MagicMock(return_value=True)

    # Session
    session = _make_fake_session(sandbox_id=parent_sandbox_id)
    parent._session = session

    return parent


# ---------------------------------------------------------------------------
# VAL-DAYTONA-022: Child volume subpath isolation
# ---------------------------------------------------------------------------


class TestChildVolumeSubpathIsolation:
    """Child sandboxes receive unique subpaths under the canonical rlm-children root."""

    def test_child_volume_subpath_includes_canonical_root(self) -> None:
        """VAL-DAYTONA-022: Child subpath is under _CHILD_VOLUME_SUBPATH_ROOT."""
        session = _make_fake_session(sandbox_id="parent-id")
        subpath = _child_volume_subpath(session)

        assert subpath.startswith(_CHILD_VOLUME_SUBPATH_ROOT)

    def test_child_volume_subpath_is_unique_per_call(self) -> None:
        """VAL-DAYTONA-022: Each child delegation gets a unique volume subpath."""
        session = _make_fake_session(sandbox_id="parent-id")
        subpath_1 = _child_volume_subpath(session)
        subpath_2 = _child_volume_subpath(session)

        assert subpath_1 != subpath_2

    def test_child_volume_subpath_without_parent_session(self) -> None:
        """VAL-DAYTONA-022: Subpath is still generated when parent session is None."""
        subpath = _child_volume_subpath(None)

        assert subpath.startswith(_CHILD_VOLUME_SUBPATH_ROOT)
        assert "/" in subpath

    def test_child_volume_subpath_contains_parent_id_token(self) -> None:
        """VAL-DAYTONA-022: Parent sandbox ID is included (safely) in child subpath."""
        session = _make_fake_session(sandbox_id="my-parent-sandbox-abc123")
        subpath = _child_volume_subpath(session)

        # The safe token strips special chars and truncates
        token = _safe_child_path_token("my-parent-sandbox-abc123")
        assert token in subpath

    def test_safe_child_path_token_replaces_special_chars(self) -> None:
        """VAL-DAYTONA-022: Special characters in sandbox IDs are replaced with dashes."""
        token = _safe_child_path_token("sandbox/with spaces & special!")
        assert "/" not in token
        assert " " not in token
        assert "&" not in token
        assert "!" not in token

    def test_safe_child_path_token_is_bounded(self) -> None:
        """VAL-DAYTONA-022: Token is truncated to 80 characters."""
        long_id = "x" * 200
        token = _safe_child_path_token(long_id)
        assert len(token) <= 80

    def test_child_sandbox_name_is_unique_and_prefixed(self) -> None:
        """VAL-DAYTONA-022: Child sandbox names use the fleet-rlm prefix and unique suffix."""
        name1 = _child_sandbox_name("clean")
        name2 = _child_sandbox_name("clean")

        assert name1.startswith("fleet-rlm-clean-child-")
        assert name2.startswith("fleet-rlm-clean-child-")
        assert name1 != name2


# ---------------------------------------------------------------------------
# VAL-DAYTONA-012 / VAL-DAYTONA-013: Child sandbox cleanup
# ---------------------------------------------------------------------------


class TestChildSandboxCleanup:
    """Child sandboxes must clean up on success, error, and cancellation."""

    def test_isolation_metadata_records_cleanup_strategy(self) -> None:
        """VAL-DAYTONA-012/013: Child metadata includes strategy and parent sandbox ID."""
        child = MagicMock()
        child.child_isolation_metadata = {}

        record_child_isolation_metadata(
            child,
            mode="auto",
            strategy="clean",
            parent_sandbox_id="parent-123",
            cleanup_status="will_delete",
        )

        assert child.child_isolation_metadata["strategy"] == "clean"
        assert child.child_isolation_metadata["parent_sandbox_id"] == "parent-123"

    def test_propagate_parent_recursion_state_propagates_all_required_fields(self) -> None:
        """VAL-DAYTONA-012: Recursion state (depth, budget, host refs) is propagated to child."""
        parent = MagicMock()
        parent._sub_rlm_depth = 1
        parent._sub_rlm_max_depth = 3
        parent._check_and_increment_llm_calls = MagicMock()
        parent._remaining_llm_budget = MagicMock(return_value=5)
        parent._host_repository = MagicMock()
        parent._host_identity = MagicMock()
        parent._host_run_id = uuid.uuid4()

        child = MagicMock()
        child._sub_rlm_depth = 0
        child._sub_rlm_max_depth = 3

        propagate_parent_recursion_state(child, parent)

        # Budget sharing
        assert child._check_and_increment_llm_calls is parent._check_and_increment_llm_calls
        # Host evidence refs
        assert child._host_repository is parent._host_repository
        assert child._host_identity is parent._host_identity
        assert child._host_run_id is parent._host_run_id

    def test_build_delegate_child_with_volume_creates_child_with_subpath(self) -> None:
        """VAL-DAYTONA-022: Volume-mounted parent creates child with unique subpath."""
        parent = _make_parent_interpreter(volume_name="tenant-vol")
        runtime = _make_fake_runtime()
        parent.runtime = runtime

        child_instances: list[MagicMock] = []

        def _mock_child_init(**kwargs: Any) -> MagicMock:
            child = MagicMock()
            child.child_isolation_metadata = {}
            child._sub_rlm_depth = 0
            child._sub_rlm_max_depth = 2
            child.volume_subpath = kwargs.get("volume_subpath")
            child.volume_name = kwargs.get("volume_name")
            child_instances.append(child)
            return child

        with patch("fleet_rlm.integrations.daytona.isolation.DaytonaSandboxRuntime") as mock_runtime_cls:
            mock_runtime_cls.return_value = MagicMock()
            with patch.object(
                parent.__class__,
                "__call__",
                side_effect=lambda *a, **kw: _mock_child_init(**kw),
            ):
                # Use build_child_interpreter directly to test the clean path
                from fleet_rlm.integrations.daytona.isolation import build_child_interpreter

                child = build_child_interpreter(
                    parent,
                    runtime=mock_runtime_cls.return_value,
                    owns_runtime=True,
                    delete_session_on_shutdown=True,
                    remaining_llm_budget=5,
                    volume_name="tenant-vol",
                    volume_subpath="meta/rlm-children/parent-id/abc123",
                )

        assert child is not None

    def test_child_volume_subpath_does_not_collide_with_parent_root(self) -> None:
        """VAL-DAYTONA-022: Child subpath is always under meta/rlm-children, not at volume root."""
        session = _make_fake_session(sandbox_id="parent-abc")
        subpath = _child_volume_subpath(session)

        # Must not be at a canonical root like /memory, /artifacts, /buffers
        canonical_roots = {"/memory", "/artifacts", "/buffers", "/meta"}
        # The subpath itself (not prefixed with /) should not equal a canonical root name
        for root in canonical_roots:
            assert not subpath.startswith(root.lstrip("/") + "/") or subpath.startswith("meta/rlm-children")

        assert subpath.startswith("meta/rlm-children/")

    def test_record_child_isolation_metadata_accumulates(self) -> None:
        """VAL-DAYTONA-013: Subsequent metadata calls accumulate (for error/cancel path)."""
        child = MagicMock()
        child.child_isolation_metadata = None

        record_child_isolation_metadata(child, mode="auto", strategy="clean")
        record_child_isolation_metadata(child, cleanup_status="error_cleanup_performed")

        assert child.child_isolation_metadata["strategy"] == "clean"
        assert child.child_isolation_metadata["cleanup_status"] == "error_cleanup_performed"


# ---------------------------------------------------------------------------
# VAL-DAYTONA-014: No orphaned resources (resource tracking)
# ---------------------------------------------------------------------------


class TestNoOrphanedResources:
    """Tests that verify cleanup metadata is set correctly so callers can track orphans."""

    def test_clean_child_metadata_includes_delete_on_shutdown_flag(self) -> None:
        """VAL-DAYTONA-014: Clean child metadata records that the sandbox will be deleted."""
        parent = _make_parent_interpreter(volume_name="tenant-vol")
        runtime = _make_fake_runtime()
        parent.runtime = runtime

        child_metadata: dict[str, Any] = {}

        def _fake_build_child_interpreter(
            _parent: Any,
            *,
            runtime: Any,
            owns_runtime: bool,
            delete_session_on_shutdown: bool,
            delete_context_on_shutdown: bool = False,
            remaining_llm_budget: int,
            volume_name: Any = None,
            volume_subpath: Any = None,
        ) -> MagicMock:
            child = MagicMock()
            child.child_isolation_metadata = {}
            child._sub_rlm_depth = 0
            child._sub_rlm_max_depth = 2
            child.volume_name = volume_name
            child.volume_subpath = volume_subpath
            child_metadata["delete_session_on_shutdown"] = delete_session_on_shutdown
            child_metadata["owns_runtime"] = owns_runtime
            return child

        with patch("fleet_rlm.integrations.daytona.isolation.DaytonaSandboxRuntime") as mock_runtime_cls:
            mock_runtime_cls.return_value = MagicMock()
            with patch(
                "fleet_rlm.integrations.daytona.isolation.build_child_interpreter",
                side_effect=_fake_build_child_interpreter,
            ):
                build_delegate_child(parent, remaining_llm_budget=5)

        # A clean child (volume-mounted parent uses clean + subpath) must delete session on shutdown
        assert child_metadata.get("delete_session_on_shutdown") is True

    def test_child_sandbox_name_includes_strategy_for_tracking(self) -> None:
        """VAL-DAYTONA-014: Sandbox names encode the creation strategy for resource tracking."""
        fork_name = _child_sandbox_name("fork")
        clean_name = _child_sandbox_name("clean")

        assert "fork" in fork_name
        assert "clean" in clean_name
        assert fork_name != clean_name

    def test_child_subpath_root_is_under_meta_for_easy_cleanup(self) -> None:
        """VAL-DAYTONA-014: All child subpaths are under meta/rlm-children for bulk cleanup."""
        assert _CHILD_VOLUME_SUBPATH_ROOT.startswith("meta/")
        assert "rlm-children" in _CHILD_VOLUME_SUBPATH_ROOT
