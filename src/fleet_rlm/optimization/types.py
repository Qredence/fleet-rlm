"""Serializable contracts for safe signature-optimization inputs and splits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OptimizationRecord:
    """One validated, redacted optimization example."""

    record_id: str
    query: str
    output_contract: dict[str, Any]
    expectations: dict[str, Any]
    execution_requirements: dict[str, Any]
    provenance: dict[str, str]
    content_sha256: str

    def optimizer_example(self) -> dict[str, Any]:
        """Return the only record material that search may receive."""
        return {
            "record_id": self.record_id,
            "query": self.query,
            "output_contract": self.output_contract,
            "expectations": self.expectations,
            "execution_requirements": self.execution_requirements,
        }


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    """Deterministic train, selection, and sealed-test partitions."""

    train: tuple[OptimizationRecord, ...]
    selection: tuple[OptimizationRecord, ...]
    sealed_test: tuple[OptimizationRecord, ...]
    seed: int

    @property
    def all_record_ids(self) -> tuple[str, ...]:
        return tuple(record.record_id for record in (*self.train, *self.selection, *self.sealed_test))

    @property
    def public_manifest(self) -> dict[str, Any]:
        """Return a manifest that does not disclose sealed-test payloads."""
        return {
            "seed": self.seed,
            "train_ids": [record.record_id for record in self.train],
            "selection_ids": [record.record_id for record in self.selection],
            "sealed_test": {
                "count": len(self.sealed_test),
                "ids_sha256": _ids_digest(record.record_id for record in self.sealed_test),
            },
        }


def _ids_digest(record_ids: Any) -> str:
    import hashlib
    import json

    payload = json.dumps(list(record_ids), separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = ["DatasetSplit", "OptimizationRecord"]
