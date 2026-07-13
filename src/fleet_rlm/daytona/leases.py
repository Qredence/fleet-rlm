"""Interpreter lease: release is idempotent and never deletes the Sandbox."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter


@dataclass(slots=True)
class InterpreterLease:
    """Acquired interpreter binding for one run."""

    sandbox_id: str
    interpreter_id: str
    volume_id: str
    mount_path: str
    interpreter: DaytonaCodeInterpreter
    session_id: str | None = None
    run_id: str | None = None
    volume_subpath: str | None = None
    delete_sandbox: Callable[[str], None] | None = None
    _released: bool = field(default=False, init=False, repr=False)
    _on_release: Callable[[], None] | None = field(default=None, init=False, repr=False)

    def release(self) -> None:
        """Shut down the interpreter context without deleting the Sandbox."""
        if self._released:
            return
        self._released = True
        try:
            self.interpreter.shutdown()
        finally:
            # Intentionally do not call delete_sandbox — SessionManager owns lifecycle.
            if self._on_release is not None:
                try:
                    self._on_release()
                except Exception:  # noqa: BLE001
                    pass
