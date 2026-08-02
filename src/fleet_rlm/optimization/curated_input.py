"""Host-owned canonical curated input for strict optimization evaluation.

A Daytona REPL cannot make an ordinary Python variable immutable: generated code
can rebind a name or mutate a detached copy.  This module therefore keeps the
canonical candidate and record on the trusted host and exposes only bounded,
JSON-serialized, detached reads through a transaction-scoped capability.

It contains no Daytona transport.  The strict evaluator may only wire its
``read`` function into a broker after the live broker-policy proof succeeds.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from fleet_rlm.optimization.types import OptimizationRecord

_SCHEMA = "fleet.curated-evaluation-input/v1"
_MAX_READ_CHARS = 8_000
_ALLOWED_POINTERS = frozenset(
    {
        "",
        "/candidate",
        "/record",
        "/record/record_id",
        "/record/query",
        "/record/output_contract",
        "/record/expectations",
        "/record/execution_requirements",
    }
)


class CuratedInputError(ValueError):
    """A caller attempted to access curated host input outside its capability."""


@dataclass(frozen=True, slots=True)
class CuratedInputHandle:
    """Untrusted-safe reference to one canonical host-side evaluation payload."""

    transaction_id: str
    sha256: str
    schema: str
    byte_size: int

    def public_value(self) -> dict[str, str | int]:
        """Return the only handle representation suitable for an untrusted REPL."""
        return {
            "transaction_id": self.transaction_id,
            "sha256": self.sha256,
            "schema": self.schema,
            "byte_size": self.byte_size,
        }


@dataclass(frozen=True, slots=True)
class CuratedInputReceipt:
    """Non-secret host receipt identifying immutable canonical evaluation input."""

    sha256: str
    schema: str = _SCHEMA


class CuratedEvaluationStore:
    """One-use host authority for canonical candidate and curated record input."""

    def __init__(self, *, candidate: str, record: OptimizationRecord) -> None:
        if not isinstance(candidate, str) or not candidate.strip():
            raise CuratedInputError("candidate instructions must be non-empty text")
        payload = {
            "candidate": candidate,
            "record": record.optimizer_example(),
        }
        encoded = _canonical_bytes(payload)
        self._payload = encoded
        self._sha256 = hashlib.sha256(encoded).hexdigest()
        self._transaction_id = secrets.token_urlsafe(24)
        self._consumed = False

    @property
    def receipt(self) -> CuratedInputReceipt:
        return CuratedInputReceipt(sha256=self._sha256)

    @property
    def handle(self) -> CuratedInputHandle:
        return CuratedInputHandle(
            transaction_id=self._transaction_id,
            sha256=self._sha256,
            schema=_SCHEMA,
            byte_size=len(self._payload),
        )

    def read(
        self,
        *,
        transaction_id: str,
        sha256: str,
        json_pointer: str = "",
        start: int = 0,
        limit: int = _MAX_READ_CHARS,
    ) -> dict[str, Any]:
        """Return one bounded detached JSON projection after capability validation."""
        self._validate_handle(transaction_id=transaction_id, sha256=sha256)
        if json_pointer not in _ALLOWED_POINTERS:
            raise CuratedInputError("curated input pointer is not permitted")
        if start < 0:
            raise CuratedInputError("curated input start must not be negative")
        if limit < 1 or limit > _MAX_READ_CHARS:
            raise CuratedInputError("curated input limit exceeds the strict maximum")
        value = _resolve_pointer(json.loads(self._payload), json_pointer)
        encoded = _canonical_bytes(value)
        chunk = encoded[start : start + limit]
        return {
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "json": chunk.decode("utf-8"),
            "start": start,
            "total_bytes": len(encoded),
            "complete": start + len(chunk) >= len(encoded),
        }

    def broker_tool(self, *, handle: CuratedInputHandle) -> Any:
        """Return the sole bounded read operation a verified broker may register."""

        def read_curated_input(
            transaction_id: str,
            sha256: str,
            json_pointer: str = "",
            start: int = 0,
            limit: int = _MAX_READ_CHARS,
        ) -> dict[str, Any]:
            return self.read(
                transaction_id=transaction_id,
                sha256=sha256,
                json_pointer=json_pointer,
                start=start,
                limit=limit,
            )

        # Capture the expected public handle in the closure so the future caller
        # must still present it and cannot use an unrelated transaction.
        expected = handle.public_value()
        if expected["transaction_id"] != self._transaction_id or expected["sha256"] != self._sha256:
            raise CuratedInputError("curated input handle does not belong to this transaction")
        return read_curated_input

    def consume(self) -> CuratedInputReceipt:
        """Seal this transaction against accidental host-side reuse."""
        if self._consumed:
            raise CuratedInputError("curated input transaction was already consumed")
        self._consumed = True
        return self.receipt

    def _validate_handle(self, *, transaction_id: str, sha256: str) -> None:
        if not secrets.compare_digest(transaction_id, self._transaction_id):
            raise CuratedInputError("unknown curated input transaction")
        if not secrets.compare_digest(sha256, self._sha256):
            raise CuratedInputError("curated input digest does not match transaction")


def canonical_curated_input(candidate: str, record: OptimizationRecord) -> Mapping[str, Any]:
    """Return a read-only host projection used only for trusted scoring/evidence."""
    store = CuratedEvaluationStore(candidate=candidate, record=record)
    return MappingProxyType(json.loads(store._payload))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if not pointer:
        return value
    for part in pointer.removeprefix("/").split("/"):
        if not isinstance(value, Mapping) or part not in value:
            raise CuratedInputError("curated input pointer does not resolve")
        value = value[part]
    return value


__all__ = [
    "CuratedEvaluationStore",
    "CuratedInputError",
    "CuratedInputHandle",
    "CuratedInputReceipt",
    "canonical_curated_input",
]
