"""Validation and deterministic partitioning for curated optimization exports."""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Mapping, Sequence
from typing import Any

from fleet_rlm.optimization.types import DatasetSplit, OptimizationRecord

MINIMUM_RECORDS = 25
EXPORT_SCHEMA = "fleet.optimization-export/v1"
_FORBIDDEN_FIELD_MARKERS = frozenset(
    {
        "attachment",
        "database",
        "db_path",
        "file_path",
        "filesystem",
        "host_path",
        "local_path",
        "raw_artifact",
        "raw_log",
        "secret",
        "token",
    }
)
_FORBIDDEN_VALUE_MARKERS = (".fleet_rlm", "/users/", "/home/", "/var/", "sqlite:", "postgresql:")
_RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class OptimizationDatasetError(ValueError):
    """A curated export violates the safe optimizer input contract."""


def load_export(payload: Mapping[str, Any]) -> list[OptimizationRecord]:
    """Validate one versioned, redacted export document."""
    if payload.get("schema") != EXPORT_SCHEMA:
        raise OptimizationDatasetError(f"export schema must be {EXPORT_SCHEMA!r}")
    records = payload.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise OptimizationDatasetError("export records must be a list")
    return validate_records(records)


def validate_records(records: Sequence[Mapping[str, Any]]) -> list[OptimizationRecord]:
    """Validate redaction, record shape, and stable identities."""
    if len(records) < MINIMUM_RECORDS:
        raise OptimizationDatasetError(f"at least {MINIMUM_RECORDS} valid records are required")

    validated: list[OptimizationRecord] = []
    seen_ids: set[str] = set()
    for raw in records:
        if not isinstance(raw, Mapping):
            raise OptimizationDatasetError("each export record must be an object")
        _reject_unsafe(raw)
        record_id = raw.get("record_id")
        if not isinstance(record_id, str) or not _RECORD_ID_PATTERN.fullmatch(record_id):
            raise OptimizationDatasetError("record_id must be a stable safe identifier")
        if record_id in seen_ids:
            raise OptimizationDatasetError(f"duplicate record_id: {record_id}")
        seen_ids.add(record_id)

        task = _required_mapping(raw, "task")
        query = task.get("query")
        if not isinstance(query, str) or not query.strip():
            raise OptimizationDatasetError(f"record {record_id}: task.query must be non-empty text")
        output_contract = _required_mapping(raw, "output_contract")
        expectations = _required_mapping(raw, "expectations")
        execution_requirements = _optional_mapping(raw, "execution_requirements")
        provenance = _required_mapping(raw, "provenance")
        redaction_version = provenance.get("redaction_version")
        if not isinstance(redaction_version, str) or not redaction_version:
            raise OptimizationDatasetError(f"record {record_id}: provenance.redaction_version is required")
        content = {
            "record_id": record_id,
            "task": {"query": query},
            "output_contract": output_contract,
            "expectations": expectations,
            "execution_requirements": execution_requirements,
            "provenance": provenance,
        }
        validated.append(
            OptimizationRecord(
                record_id=record_id,
                query=query,
                output_contract=output_contract,
                expectations=expectations,
                execution_requirements=execution_requirements,
                provenance={str(key): str(value) for key, value in provenance.items()},
                content_sha256=_digest(content),
            )
        )
    return validated


def split_records(records: Sequence[OptimizationRecord], *, seed: int) -> DatasetSplit:
    """Create an ID-stable 60/20/20 train, selection, sealed-test split."""
    if len(records) < MINIMUM_RECORDS:
        raise OptimizationDatasetError(f"at least {MINIMUM_RECORDS} valid records are required")
    canonical = sorted(records, key=lambda record: record.record_id)
    shuffled = list(canonical)
    random.Random(seed).shuffle(shuffled)
    total = len(shuffled)
    train_count = total * 3 // 5
    selection_count = total // 5
    sealed_count = total - train_count - selection_count
    if min(train_count, selection_count, sealed_count) < 5:
        raise OptimizationDatasetError("split must contain at least five records in every partition")
    return DatasetSplit(
        train=tuple(shuffled[:train_count]),
        selection=tuple(shuffled[train_count : train_count + selection_count]),
        sealed_test=tuple(shuffled[train_count + selection_count :]),
        seed=seed,
    )


def _required_mapping(raw: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = raw.get(field)
    if not isinstance(value, Mapping):
        raise OptimizationDatasetError(f"{field} must be an object")
    return dict(value)


def _optional_mapping(raw: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = raw.get(field, {})
    if not isinstance(value, Mapping):
        raise OptimizationDatasetError(f"{field} must be an object when supplied")
    return dict(value)


def _reject_unsafe(value: Any, *, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(marker in normalized for marker in _FORBIDDEN_FIELD_MARKERS):
                raise OptimizationDatasetError(f"forbidden raw-state field: {path}{key}")
            _reject_unsafe(child, path=f"{path}{key}.")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_unsafe(child, path=path)
    elif isinstance(value, str):
        normalized = value.lower()
        if any(marker in normalized for marker in _FORBIDDEN_VALUE_MARKERS):
            raise OptimizationDatasetError(f"forbidden raw-state reference in {path.rstrip('.') or 'record'}")


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "EXPORT_SCHEMA",
    "MINIMUM_RECORDS",
    "OptimizationDatasetError",
    "load_export",
    "split_records",
    "validate_records",
]
