"""Minimal copyable role LM for tests that never reach a provider."""

from __future__ import annotations

from typing import Any


class PlaceholderRoleLM:
    """A copyable stand-in for a role LM.

    ``RLMModelBundle.bind_turn`` hands every role LM to DSPy ``copy()`` so that
    per-Turn history and usage stay isolated; a double without ``copy()`` cannot
    be bound to a Turn. Use this instead of a bare ``object()`` in tests that
    exercise preparation, tracing, or history plumbing rather than a provider.
    """

    def __init__(self, model: str = "testing/placeholder") -> None:
        self.model = model
        self.history: list[Any] = []

    def copy(self, **kwargs: Any) -> PlaceholderRoleLM:
        del kwargs
        return type(self)(self.model)


def placeholder_bundle() -> Any:
    """Return a bound-ready root/sub model bundle for plumbing tests."""
    from fleet_rlm.rlm.program import RLMModelBundle

    return RLMModelBundle(PlaceholderRoleLM("testing/root"), PlaceholderRoleLM("testing/sub"))


__all__ = ["PlaceholderRoleLM", "placeholder_bundle"]
