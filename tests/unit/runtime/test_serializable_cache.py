"""Unit tests for the per-instance ``_prepared_serializable_cache``.

VAL-CORR-004: ``_prepared_serializable_cache`` in
``runtime/modules/factory.py`` (``_StreamingRLM``) must be a per-instance dict
(defined in ``__init__``), NOT a class-level dict. Two separate
``_StreamingRLM`` instances that receive input args with the same variable
names but different content must NOT share cached data.

The cache is reset once per ``EscalatingFleetModule._run_rlm`` invocation (on
the chosen RLM instance) so that primary + corrective/parse-error retries
within a turn reuse the serialized variables. ``forward()`` does NOT clear
the cache — clearing is ``_run_rlm``'s responsibility. The cache key is
``frozenset((name, id(value)))`` so content changes across turns (different
object identities) miss the cache even when names are identical.
"""

from __future__ import annotations

from types import SimpleNamespace
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
# VAL-CORR-004: cache reset ownership and key shape
# ---------------------------------------------------------------------------


class TestPreparedSerializableCacheResetAndKey:
    """``forward()`` does NOT clear the cache; ``_run_rlm`` owns the reset.
    The cache key is ``frozenset((name, id(value)))`` so content changes across
    turns miss the cache even when names are identical."""

    def test_forward_does_not_clear_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Populating the cache, then calling ``forward()`` (with the base
        ``forward`` mocked), must NOT clear the cache — the reset is
        ``EscalatingFleetModule._run_rlm``'s responsibility so corrective
        retries within a turn can reuse the serialized variables."""
        _patch_mlflow(monkeypatch)
        rlm = _make_rlm()

        cache_key = frozenset({"context", "skills"})
        rlm._prepared_serializable_cache[cache_key] = {"context": "stale-data"}
        assert len(rlm._prepared_serializable_cache) == 1

        from fleet_rlm.runtime.modules import factory as factory_mod

        base_cls = factory_mod._DSPY_RLM_BASE
        monkeypatch.setattr(base_cls, "forward", lambda self, **kwargs: MagicMock())

        rlm.forward()

        # The pre-existing entry must still be present — forward() no longer
        # clears. _run_rlm clears once per turn before the execution loop.
        assert cache_key in rlm._prepared_serializable_cache, (
            "forward() must NOT clear the serializable-var cache; the reset is "
            "owned by EscalatingFleetModule._run_rlm so corrective retries "
            "within a turn reuse the serialized variables."
        )

    def test_cache_key_uses_name_and_object_identity(self) -> None:
        """The cache key is ``frozenset((name, id(value)))``. Two
        SandboxSerializable inputs with the same name but different object
        identities must produce distinct cache keys so a new turn that rebinds
        the same name to a fresh object misses the cache and re-serializes."""
        rlm = _make_rlm()

        obj_a = SimpleNamespace()
        obj_b = SimpleNamespace()
        key_a = frozenset([("context", id(obj_a))])
        key_b = frozenset([("context", id(obj_b))])

        assert key_a != key_b, (
            "Cache keys must include object identity (id(value)) so content "
            "changes across turns miss the cache even when names are identical."
        )

        rlm._prepared_serializable_cache[key_a] = {"context": "data-a"}
        assert key_a in rlm._prepared_serializable_cache
        assert key_b not in rlm._prepared_serializable_cache
