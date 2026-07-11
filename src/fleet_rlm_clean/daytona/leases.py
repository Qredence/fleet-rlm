"""Interpreter lease: release is idempotent and never deletes the Sandbox."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter


@dataclass(slots=True)
class InterpreterLease:
    """Acquired interpreter binding for one run."""

    sandbox_id: str
    interpreter_id: str
    volume_id: str
    mount_path: str
    interpreter: DaytonaCodeInterpreter
    delete_sandbox: Callable[[str], None] | None = None
    _released: bool = field(default=False, init=False, repr=False)

    def release(self) -> None:
        """Shut down the interpreter context without deleting the Sandbox."""
        if self._released:
            return
        self._released = True
        self.interpreter.shutdown()
        # Intentionally do not call delete_sandbox — SessionManager owns lifecycle.
