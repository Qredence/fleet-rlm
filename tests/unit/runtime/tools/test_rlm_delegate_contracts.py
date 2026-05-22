"""VAL-RLM contract tests for host-side recursive delegation.

Covers assertions that complement the baseline tests in test_rlm_delegate.py:

- VAL-RLM-007: Budget exhaustion short-circuits before any side effects
- VAL-RLM-010: Degraded/failed child results are persisted, not just logged
- VAL-RLM-017: All children cleaned up including review-needed and error cases
- VAL-RLM-018: All host recursive entrypoints produce canonical child metadata
"""

from __future__ import annotations

from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.tools import rlm_delegate as rlm_delegate_mod

# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


class _FakeChild:
    def __init__(self, *, started: bool = False) -> None:
        self._started = started
        self.verbose = False
        self.sub_lm = None
        self.repo_url: str | None = None
        self.volume_name: str | None = None
        self.rlm_max_iterations = 20
        self.child_isolation_metadata: dict[str, Any] = {
            "mode": "auto",
            "strategy": "clean",
            "child_sandbox_id": "sbx-child-test",
        }
        self.start_calls = 0
        self.shutdown_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        self._started = True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._started = False


class _FakeParent:
    def __init__(
        self,
        children: list[_FakeChild] | None = None,
        *,
        remaining: int = 50,
    ) -> None:
        self._children = children or []
        self._child_index = 0
        self.remaining = remaining
        self.verbose = False
        self.build_calls: list[int] = []
        self.lease_calls: list[int] = []
        self._host_repository: Any | None = None
        self._host_identity: Any | None = None
        self._host_run_id: Any | None = None

    def _remaining_llm_budget(self) -> int:
        return self.remaining

    def build_delegate_child(self, *, remaining_llm_budget: int) -> _FakeChild:
        self.build_calls.append(remaining_llm_budget)
        if self._child_index < len(self._children):
            child = self._children[self._child_index]
        else:
            child = _FakeChild(started=True)
        self._child_index += 1
        return child

    def _install_child_budget_lease(self, child: _FakeChild, lease: int) -> None:
        self.lease_calls.append(lease)
        child.max_llm_calls = lease


class _FakeRepository:
    def __init__(self) -> None:
        self.store_calls: list[dict[str, Any]] = []

    async def store_rlm_trace(self, **kwargs: Any) -> None:
        self.store_calls.append(kwargs)


class _FakeIdentity:
    tenant_id = "t-test"
    workspace_id = "ws-test"


# ---------------------------------------------------------------------------
# VAL-RLM-007: Budget exhaustion short-circuits before ALL side effects
# ---------------------------------------------------------------------------


class TestBudgetExhaustionBeforeSideEffects:
    """VAL-RLM-007: Zero remaining budget → no child sandbox, no LLM call, no staged artifact."""

    def test_no_build_delegate_child_called(self) -> None:
        """delegate_to_rlm: build_delegate_child must NOT be called on budget=0."""
        parent = _FakeParent(remaining=0)
        result = rlm_delegate_mod.delegate_to_rlm("query", interpreter=parent)

        assert result["status"] == "error"
        assert result["reason"] == "budget_exhausted"
        assert parent.build_calls == [], "build_delegate_child must not be called when budget is exhausted"

    def test_batched_no_build_delegate_child_called(self) -> None:
        """delegate_to_rlm_batched: no children constructed when budget < child count."""
        parent = _FakeParent(remaining=2)
        result = rlm_delegate_mod.delegate_to_rlm_batched(["a", "b", "c"], interpreter=parent)

        assert result["status"] == "error"
        assert result["reason"] == "budget_exhausted"
        assert parent.build_calls == [], "no child must be built when budget is exhausted"

    def test_batched_per_query_error_on_budget_exhaustion(self) -> None:
        """Each query in a batch gets an individual budget_exhausted error entry."""
        parent = _FakeParent(remaining=1)
        result = rlm_delegate_mod.delegate_to_rlm_batched(["x", "y"], interpreter=parent)

        assert result["status"] == "error"
        errors = result.get("errors", [])
        assert len(errors) == 2
        for error in errors:
            assert error["reason"] == "budget_exhausted"


# ---------------------------------------------------------------------------
# VAL-RLM-010: Degraded/failed child results are persisted instead of discarded
# ---------------------------------------------------------------------------


class TestDegradedChildResultsPersisted:
    """VAL-RLM-010: Error and degraded child outcomes must be persisted."""

    def _parent_with_repository(self, child: _FakeChild, remaining: int = 50) -> _FakeParent:
        parent = _FakeParent([child], remaining=remaining)
        repository = _FakeRepository()
        identity = _FakeIdentity()
        parent._host_repository = repository
        parent._host_identity = identity
        parent._host_run_id = "run-test-persist"
        return parent

    def test_exception_in_child_rlm_persists_error_trace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When child RLM raises, _persist_child_trace_error must be called."""
        child = _FakeChild(started=True)
        parent = self._parent_with_repository(child)

        def _failing_rlm(**kwargs: Any) -> Any:
            raise RuntimeError("forced child failure")

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: _failing_rlm,
        )

        result = rlm_delegate_mod.delegate_to_rlm("fail query", interpreter=parent)

        assert result["status"] == "error"
        assert "forced child failure" in result["error"]
        # Verify repository was called with error payload
        repo = parent._host_repository
        assert len(repo.store_calls) == 1, "store_rlm_trace must be called on child exception"
        call = repo.store_calls[0]
        assert call["run_id"] == "run-test-persist"
        assert call["tenant_id"] == "t-test"
        payload = call["payload_json"]
        assert payload["status"] == "error"
        assert "forced child failure" in payload["error"]
        assert "query" in payload

    def test_exception_trace_id_is_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Error trace IDs use the rlm-child-err- prefix for distinguishability."""
        child = _FakeChild(started=True)
        parent = self._parent_with_repository(child)

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: (_ for _ in ()).throw(RuntimeError("err")),
        )

        rlm_delegate_mod.delegate_to_rlm("query", interpreter=parent)

        repo = parent._host_repository
        if repo.store_calls:
            assert repo.store_calls[0]["trace_id"].startswith("rlm-child-err-")

    def test_null_answer_also_persists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Null-answer (no SUBMIT) child results are persisted via _persist_child_trace."""
        child = _FakeChild(started=True)
        parent = self._parent_with_repository(child)
        prediction = dspy.Prediction(answer=None)

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: prediction,
        )

        result = rlm_delegate_mod.delegate_to_rlm("no-submit query", interpreter=parent)

        assert result["status"] == "error"
        assert result["reason"] == "null_answer"
        repo = parent._host_repository
        # _persist_child_trace is called with answer="" and the prediction object
        assert len(repo.store_calls) == 1

    def test_broker_degraded_also_persists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Broker-marked answers still have trace persistence before review return."""
        child = _FakeChild(started=True)
        parent = self._parent_with_repository(child)
        prediction = dspy.Prediction(answer="degraded answer")
        prediction.trajectory = [{"output": "[Error] Broker server failed to start within timeout"}]

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: prediction,
        )

        result = rlm_delegate_mod.delegate_to_rlm("broker query", interpreter=parent)

        assert result["status"] == "needs_human_review"
        repo = parent._host_repository
        assert len(repo.store_calls) == 1

    def test_no_persistence_without_host_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Delegation without repository context does not crash."""
        child = _FakeChild(started=True)
        parent = _FakeParent([child], remaining=50)
        # No _host_repository set

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: (_ for _ in ()).throw(RuntimeError("no-repo")),
        )

        # Should not raise — just logs
        result = rlm_delegate_mod.delegate_to_rlm("query", interpreter=parent)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# VAL-RLM-017: All children cleaned up including error and review-needed cases
# ---------------------------------------------------------------------------


class TestAllChildrenCleanedUp:
    """VAL-RLM-017: shutdown() called for every child on success, error, and review."""

    def test_all_successful_children_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every child in a successful batch is shut down."""
        children = [_FakeChild(started=True) for _ in range(3)]
        parent = _FakeParent(children, remaining=9)

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: dspy.Prediction(answer=f"ok:{kw['prompt']}"),
        )

        result = rlm_delegate_mod.delegate_to_rlm_batched(["a", "b", "c"], interpreter=parent)
        assert result["status"] == "ok"
        assert all(c.shutdown_calls == 1 for c in children), "all successful children must be shut down"

    def test_errored_child_still_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Children that raise exceptions are still shut down."""
        bad_child = _FakeChild(started=True)
        good_child = _FakeChild(started=True)
        parent = _FakeParent([bad_child, good_child], remaining=10)

        call_count = 0

        def _mixed_rlm(**kwargs: Any) -> Any:
            nonlocal call_count
            child = kwargs["interpreter"]
            call_count += 1
            if child is bad_child:
                raise RuntimeError("child error")
            return dspy.Prediction(answer="good")

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: _mixed_rlm(**kwargs, **kw),
        )

        result = rlm_delegate_mod.delegate_to_rlm_batched(["bad", "good"], interpreter=parent)
        assert result["status"] == "error"
        # Both children must be shut down regardless of their outcome
        assert bad_child.shutdown_calls == 1, "errored child must still be shut down"
        assert good_child.shutdown_calls == 1, "successful sibling must also be shut down"

    def test_review_needed_child_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Children with broker markers (review-needed) are still shut down."""
        child = _FakeChild(started=True)
        parent = _FakeParent([child], remaining=5)

        prediction = dspy.Prediction(answer="usable answer")
        prediction.trajectory = [{"output": "[Error] Broker server failed to start within timeout"}]

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: prediction,
        )

        result = rlm_delegate_mod.delegate_to_rlm("query", interpreter=parent)
        assert result["status"] == "needs_human_review"
        assert child.shutdown_calls == 1, "review-needed child must still be shut down"

    def test_single_error_child_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single delegate_to_rlm failures still trigger child shutdown."""
        child = _FakeChild(started=True)
        parent = _FakeParent([child], remaining=5)

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: (_ for _ in ()).throw(RuntimeError("single fail")),
        )

        result = rlm_delegate_mod.delegate_to_rlm("query", interpreter=parent)
        assert result["status"] == "error"
        assert child.shutdown_calls == 1, "child must be shut down even on exception"


# ---------------------------------------------------------------------------
# VAL-RLM-018: All host recursive entrypoints use canonical child metadata
# ---------------------------------------------------------------------------


class TestCanonicalChildIsolationMetadata:
    """VAL-RLM-018: Both host entrypoints produce canonical child isolation metadata."""

    def test_single_delegation_records_child_sandbox_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delegate_to_rlm records child_sandbox_id in isolation metadata."""
        child = _FakeChild(started=True)
        parent = _FakeParent([child], remaining=10)

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: dspy.Prediction(answer="ok"),
        )

        result = rlm_delegate_mod.delegate_to_rlm("query", interpreter=parent)
        assert result["status"] == "ok"
        meta = child.child_isolation_metadata
        assert "mode" in meta
        assert "strategy" in meta
        assert "child_sandbox_id" in meta

    def test_batched_delegation_leases_budget_to_every_child(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delegate_to_rlm_batched propagates budget lease to every child's metadata."""
        children = [_FakeChild(started=True) for _ in range(3)]
        parent = _FakeParent(children, remaining=6)

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: dspy.Prediction(answer=f"ok:{kw['prompt']}"),
        )

        result = rlm_delegate_mod.delegate_to_rlm_batched(["a", "b", "c"], interpreter=parent)
        assert result["status"] == "ok"
        for child in children:
            assert "llm_budget_lease" in child.child_isolation_metadata, (
                f"child metadata missing llm_budget_lease: {child.child_isolation_metadata}"
            )

    def test_cleanup_status_recorded_on_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful shutdown records cleanup_status='deleted' in child metadata."""
        child = _FakeChild(started=True)
        parent = _FakeParent([child], remaining=5)

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: dspy.Prediction(answer="done"),
        )

        rlm_delegate_mod.delegate_to_rlm("query", interpreter=parent)
        assert child.child_isolation_metadata.get("cleanup_status") == "deleted"

    def test_both_entrypoints_call_build_delegate_child(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both host entrypoints route through build_delegate_child."""
        children_single = [_FakeChild(started=True)]
        parent_single = _FakeParent(children_single, remaining=10)

        children_batch = [_FakeChild(started=True), _FakeChild(started=True)]
        parent_batch = _FakeParent(children_batch, remaining=10)

        monkeypatch.setattr(
            rlm_delegate_mod,
            "build_recursive_subquery_rlm",
            lambda **kwargs: lambda **kw: dspy.Prediction(answer="x"),
        )

        rlm_delegate_mod.delegate_to_rlm("q", interpreter=parent_single)
        assert len(parent_single.build_calls) == 1

        rlm_delegate_mod.delegate_to_rlm_batched(["a", "b"], interpreter=parent_batch)
        assert len(parent_batch.build_calls) == 2
