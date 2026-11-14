"""Strict host envelope around canonical native FleetProgram state."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from typing import Any

from fleet_rlm.engine.programs import FleetProgram

PROGRAM_SCHEMA = "fleet-program-b2"
_IDENTITY = {
    "format": "fleet-program",
    "format_version": 2,
    "program_schema": PROGRAM_SCHEMA,
    "child_policy_contract": 1,
}


def program_envelope(program: FleetProgram, *, metadata: dict[str, str] | None = None) -> bytes:
    """Export the root deliberately; wrapper-prefixed native saves are not installable."""
    if not isinstance(program, FleetProgram):
        raise TypeError("Compiled export requires an unwrapped FleetProgram")
    state = program.dump_state()
    program.deepcopy().load_state(state)
    return json.dumps({**_IDENTITY, "state": state, "metadata": metadata or {}}, indent=2, sort_keys=True).encode()


def load_program_envelope(program: FleetProgram, content: bytes) -> None:
    """Validate identity and complete native state before atomically applying any component."""
    if len(content) > CompiledProgramFile.MAX_BYTES:
        raise ValueError("Compiled program exceeds the 10 MB size limit")
    envelope: Any = json.loads(content)
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {*_IDENTITY, "state", "metadata"}
        or any(type(envelope[key]) is not type(value) or envelope[key] != value for key, value in _IDENTITY.items())
        or not isinstance(envelope["metadata"], dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in envelope["metadata"].items())
    ):
        raise ValueError("Unsupported compiled program: select fleet-program-b2 or explicit baseline")
    program.load_state(envelope["state"])


def installed_program_error(digest: str, ledger: Mapping[str, Any]) -> str | None:
    """Why the program in use contradicts the ledger, or None.

    `ledger` is `Store.approved_program()`. Once any global job was approved, only the file that
    approval installed (or an approval in flight) may serve; baseline is correct only when none was.
    Per-Session artifacts from before the program became global are never consulted.
    """
    approved = ledger.get("approved")
    if approved and digest not in {approved, *ledger.get("approving", ())}:
        return (
            "The approved global program is missing or changed: fleet.json does not match the latest "
            "approved job. Restore that file or approve a new candidate."
        )
    return None


def installed_program_problem(path: str, ledger: Mapping[str, Any]) -> str | None:
    """`installed_program_error` for the file at `path`; an unreadable file is a problem, not an exception."""
    try:
        digest = hashlib.sha256(CompiledProgramFile(path).read()).hexdigest() if os.path.lexists(path) else ""
    except Exception:
        return (
            "The installed program fleet.json cannot be read as a regular file. Restore it or approve a new candidate."
        )
    return installed_program_error(digest, ledger)


class CompiledProgramFile:
    """Bounded regular-file loading: a file that exists must be valid; absence is baseline.

    Whether absence is legitimate is the ledger's call (`installed_program_error`), not this class's.
    """

    MAX_BYTES = 10_000_000

    def __init__(self, path: str | None, *, baseline: bool = False) -> None:
        self.path = path
        self.digest = ""
        self._baseline = baseline or path is None

    def load_initial(self, program: FleetProgram) -> None:
        """None explicitly selects baseline; a watched new-session baseline may await installation."""
        if self.path is not None and not self._baseline:
            self._apply(program, self.read())

    def reload_if_changed(self, program: FleetProgram) -> None:
        """Verify file content at serialized Turn boundaries; reload when its digest changes."""
        if self.path is None or (self._baseline and not os.path.lexists(self.path)):
            return
        content = self.read()
        if hashlib.sha256(content).hexdigest() != self.digest:
            self._apply(program, content)

    def read(self) -> bytes:
        """Read bounded regular-file bytes for either selection or candidate approval."""
        assert self.path is not None
        # The descriptor prevents a path swap between the regular-file check and read.
        if os.path.islink(self.path):
            raise ValueError("Compiled program path must be a regular file")
        with os.fdopen(os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Compiled program path must be a regular file")
            content = source.read(self.MAX_BYTES + 1)
        if len(content) > self.MAX_BYTES:
            raise ValueError("Compiled program exceeds the 10 MB size limit")
        return content

    def _apply(
        self,
        program: FleetProgram,
        content: bytes,
    ) -> None:
        load_program_envelope(program, content)
        self.digest = hashlib.sha256(content).hexdigest()
        self._baseline = False
