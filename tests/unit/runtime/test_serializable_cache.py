"""Unit tests for the per-instance ``_prepared_serializable_cache``.

VAL-CORR-004: ``_prepared_serializable_cache`` in
``runtime/modules/factory.py`` (``_StreamingRLM``) must be a per-instance dict
(defined in ``__init__``), NOT a class-level dict. Two separate
``_StreamingRLM`` instances that receive input args with the same variable
names but different content must NOT share cached data. Additionally, the
cache must be cleared at the start of each ``forward()`` call so that stale
data from a previous turn does not leak into the next turn.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fleet_rlm.integrations.observability import mlflow_context
from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
from fleet_rlm.runtime.modules.factory import _StreamingRLM, create_runtime_rlm

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable real MLflow span emission so tests run without an MLflow server."""
    fake_span = MagicMock()
    fake_span.__enter__ = MagicMock(return_value=fake_span)
    fake_span.__exit__ = MagicMock(return_value=None)
    monkeypatch.setattr(mlflow_context, "mlflow_child_span", MagicMock(return_value=fake_span))
    monkeypatch.setattr(mlflow_context, "set_mlflow_span_outputs", MagicMock())


def _make_rlm(*, max_iterations: int = 3) -> _StreamingRLM:
    """Build a real ``_StreamingRLM`` with a stub interpreter."""
    interpreter = SimpleNamespace(_turn_step_callback=lambda _payload: None)
    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=10,
        verbose=False,
    )
    assert isinstance(rlm, _StreamingRLM)
    return rlm


# ---------------------------------------------------------------------------
# VAL-CORR-004: per-instance isolation
# ---------------------------------------------------------------------------


class TestPreparedSerializableCachePerInstance:
    """``_prepared_serializable_cache`` is per-instance, not class-level."""

    def test_cache_is_instance_attribute_not_class_attribute(self) -> None:
        """The cache must be defined in ``__init__`` (instance attribute), not
        as a class-level attribute. A class-level dict would be shared across
        all instances via the class object."""
        assert not hasattr(_StreamingRLM, "_prepared_serializable_cache"), (
            "_prepared_serializable_cache must not be a class-level attribute; "
            "it should be assigned in __init__ as a per-instance dict."
        )

    def test_two_instances_have_distinct_cache_dicts(self) -> None:
        """Two ``_StreamingRLM`` instances must have distinct cache dict
        objects (``id(a.cache) != id(b.cache)``)."""
        rlm_a = _make_rlm()
        rlm_b = _make_rlm()
        assert rlm_a._prepared_serializable_cache is not rlm_b._prepared_serializable_cache
        assert id(rlm_a._prepared_serializable_cache) != id(rlm_b._prepared_serializable_cache)

    def test_populating_instance_a_cache_does_not_leak_to_instance_b(self) -> None:
        """Populating instance A's cache with key
        ``frozenset({"context", "skills"})`` must NOT make the entry visible
        in instance B's cache."""
        rlm_a = _make_rlm()
        rlm_b = _make_rlm()

        cache_key = frozenset({"context", "skills"})
        rlm_a._prepared_serializable_cache[cache_key] = {"context": "data-from-A"}

        # Instance A has the entry
        assert cache_key in rlm_a._prepared_serializable_cache
        # Instance B does NOT have the entry (no shared class-level dict)
        assert cache_key not in rlm_b._prepared_serializable_cache
        assert len(rlm_b._prepared_serializable_cache) == 0

    def test_cache_starts_empty_per_instance(self) -> None:
        """Each new instance starts with an empty cache."""
        rlm = _make_rlm()
        assert rlm._prepared_serializable_cache == {}
        assert len(rlm._prepared_serializable_cache) == 0


# ---------------------------------------------------------------------------
# VAL-CORR-004: cache cleared at start of each forward() call
# ---------------------------------------------------------------------------


class TestPreparedSerializableCacheClearedPerForward:
    """The cache is cleared at the start of each ``forward()`` call."""

    def test_forward_clears_cache_at_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Populating the cache, then calling ``forward()`` (with the base
        ``forward`` mocked so it does not execute the full RLM loop), must
        leave the cache empty — confirming the clear-at-start logic runs
        before the base ``forward`` body."""
        _patch_mlflow(monkeypatch)
        rlm = _make_rlm()

        # Pre-populate the cache as if a previous turn left data behind.
        cache_key = frozenset({"context", "skills"})
        rlm._prepared_serializable_cache[cache_key] = {"context": "stale-data"}
        assert len(rlm._prepared_serializable_cache) == 1

        # Mock the base class forward so we don't need a full RLM run, but
        # still exercise the real _StreamingRLM.forward entry point which
        # contains the cache-clear logic. The base forward is invoked via
        # super().forward(**input_args) inside _StreamingRLM.forward.
        from fleet_rlm.runtime.modules import factory as factory_mod

        base_cls = factory_mod._DSPY_RLM_BASE
        monkeypatch.setattr(base_cls, "forward", lambda self, **kwargs: MagicMock())

        # Call forward; the clear-at-start should empty the cache before
        # (or regardless of) what the base forward does.
        rlm.forward()

        assert rlm._prepared_serializable_cache == {}, (
            "Cache must be cleared at the start of each forward() call so "
            "stale data from a previous turn does not leak into the next turn."
        )

    def test_forward_clears_cache_even_when_populated_within_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After ``forward()`` returns, the cache reflects only data populated
        during that same ``forward()`` call (the clear happens at the start,
        so within-call population is preserved until the next call clears it)."""
        _patch_mlflow(monkeypatch)
        rlm = _make_rlm()

        # Populate cache from a "previous turn".
        rlm._prepared_serializable_cache[frozenset({"old"})] = {"old": "data"}
        assert len(rlm._prepared_serializable_cache) == 1

        captured: list[dict[str, Any]] = []

        def fake_base_forward(self: Any, **kwargs: Any) -> Any:
            # Simulate within-call population after the clear has run.
            self._prepared_serializable_cache[frozenset({"new"})] = {"new": "data"}
            captured.append(dict(self._prepared_serializable_cache))
            return MagicMock()

        from fleet_rlm.runtime.modules import factory as factory_mod

        monkeypatch.setattr(factory_mod._DSPY_RLM_BASE, "forward", fake_base_forward)

        rlm.forward()

        # Within the call (after clear, during base forward), only the new
        # entry should be present — the old entry must have been cleared.
        assert len(captured) == 1
        assert frozenset({"old"}) not in captured[0]
        assert frozenset({"new"}) in captured[0]
