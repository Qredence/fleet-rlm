"""Single place that appends callbacks to global DSPy settings.

DSPy only allows ``dspy.configure`` from the thread (and async task) that
first called it, while fleet-rlm registers observability callbacks from
worker threads during runtime-service warmup. This module owns the one
sanctioned fallback for that case so no other code touches DSPy's settings
storage directly.
"""

from __future__ import annotations

from typing import Any

_OWNERSHIP_ERROR_MARKERS = (
    "dspy.settings can only be changed by the thread",
    "can only be called from the same async task",
)


def ensure_dspy_callbacks(callbacks_to_add: list[Any]) -> None:
    """Append callbacks to ``dspy.settings.callbacks``, deduplicated by type.

    Safe to call from any thread: prefers ``dspy.configure`` and falls back
    to mutating DSPy's main-thread config under the settings lock when the
    caller is not the settings owner. If the current thread already has an
    explicit callbacks override, patch that override too so immediately
    following DSPy work can see the registered callback.
    """
    import dspy

    existing = list(getattr(dspy.settings, "callbacks", []) or [])
    pending = [cb for cb in callbacks_to_add if not any(isinstance(ex, type(cb)) for ex in existing)]
    if not pending:
        return

    try:
        dspy.configure(callbacks=[*existing, *pending])
        return
    except RuntimeError as exc:
        if not any(marker in str(exc) for marker in _OWNERSHIP_ERROR_MARKERS):
            raise

    from dspy.dsp.utils import settings as dspy_settings_module

    with dspy.settings.lock:
        main_config = dspy_settings_module.main_thread_config
        current = list(main_config.get("callbacks", []) or [])
        registered: list[Any] = []
        for callback in pending:
            existing = next((ex for ex in current if isinstance(ex, type(callback))), None)
            if existing is None:
                current.append(callback)
                registered.append(callback)
            else:
                registered.append(existing)
        main_config["callbacks"] = current

        thread_local_overrides = getattr(dspy_settings_module, "thread_local_overrides", None)
        if thread_local_overrides is None:
            return
        active_overrides = thread_local_overrides.get()
        if "callbacks" not in active_overrides:
            return
        active_callbacks = list(active_overrides.get("callbacks", []) or [])
        changed = False
        for callback in registered:
            if not any(isinstance(ex, type(callback)) for ex in active_callbacks):
                active_callbacks.append(callback)
                changed = True
        if changed:
            active_overrides["callbacks"] = active_callbacks
