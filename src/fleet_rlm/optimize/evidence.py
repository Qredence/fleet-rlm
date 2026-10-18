"""Declarative gold checks and bounded, host-collected execution evidence.

Gold is never a program input. Collectors observe files and lifecycle receipts;
only the scorer compares those observations with reviewed expectations.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_EVIDENCE_BYTES = 256_000
MAX_ARTIFACT_BYTES = 64_000
MAX_ARTIFACTS = 32


def artifact_path(value: str) -> str:
    """Return value when it is a normalized relative path under artifacts/, else raise."""
    path = PurePosixPath(value)
    if (
        not value.startswith("artifacts/")
        or str(path) != value
        or ".." in path.parts
        or "\\" in value
        or len(path.parts) < 2
    ):
        raise ValueError("Expected a normalized path under artifacts/")
    return value


class StrictModel(BaseModel):
    """Pydantic base that forbids unknown fields and type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ArtifactExpectation(StrictModel):
    """Gold for one artifact under artifacts/, scored by its declared kind."""

    path: str
    kind: Literal["text", "json", "csv", "python"]
    expected: Any = None
    key_columns: list[str] = Field(default_factory=list, max_length=10)
    tolerance: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    execute: bool = False
    timeout_seconds: Annotated[int, Field(ge=1, le=60)] = 30
    exit_code: int = 0
    stdout: str | None = None

    @model_validator(mode="after")
    def check(self) -> ArtifactExpectation:
        """Reject kind, path, and execution combinations that cannot be scored."""
        artifact_path(self.path)
        if self.kind == "python":
            if not self.path.endswith(".py") or not self.execute:
                raise ValueError("Python expectations require a .py path and execute=true")
        elif self.execute:
            raise ValueError("Only Python artifacts can execute")
        elif self.kind == "text" and not isinstance(self.expected, str):
            raise ValueError("Text expectations require expected text")
        elif self.kind == "csv":
            if not self.key_columns or len(set(self.key_columns)) != len(self.key_columns):
                raise ValueError("CSV expectations require unique key_columns")
            if not isinstance(self.expected, list) or not self.expected:
                raise ValueError("CSV expectations require expected rows")
            if any(not isinstance(row, dict) or not set(self.key_columns) <= row.keys() for row in self.expected):
                raise ValueError("CSV expected rows must include their key columns")
        return self


class ChildExpectation(StrictModel):
    """Gold for one required successful child: its input paths and promoted artifacts."""

    input_paths: list[str] = Field(max_length=100)
    artifacts: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def check(self) -> ChildExpectation:
        """Validate normalized child inputs, unique inputs, and artifact suffixes."""
        from fleet_rlm.workspace import _child_input_path

        if any(_child_input_path(path) != path for path in self.input_paths):
            raise ValueError("Child inputs must be normalized workspace paths")
        if len(set(self.input_paths)) != len(self.input_paths):
            raise ValueError("Duplicate child input")
        for path in self.artifacts:
            artifact_path("artifacts/" + path)
        return self


class StructuredExpectations(StrictModel):
    """Versioned gold for declared artifacts and required child sandboxes."""

    schema_version: Literal[1] = 1
    artifacts: list[ArtifactExpectation] = Field(default_factory=list, max_length=MAX_ARTIFACTS)
    # One entry per required successful child; duplicate entries represent multiple children.
    children: list[ChildExpectation] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def check(self) -> StructuredExpectations:
        """Reject duplicate artifact expectations."""
        paths = [item.path for item in self.artifacts]
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate artifact expectation")
        return self


def validate_structured(value: Any) -> dict[str, Any]:
    """Validate reviewed gold against the schema and the evidence size limit.

    Empty input means no checks; non-JSON values and nonfinite numbers are rejected.
    """
    if value == {}:
        return {}
    if not isinstance(value, dict):
        raise ValueError("structured_expectations must be an object")
    # Reject non-JSON values and nonfinite numbers as well as unbounded gold payloads.
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_EVIDENCE_BYTES:
        raise ValueError("Structured expectations exceed the size limit")
    return StructuredExpectations.model_validate(value).model_dump()


def verification_entries(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Return execution configuration only; expected results stay on the scoring side."""
    parsed = validate_structured(value)
    return [
        {"path": item["path"], "timeout_seconds": item["timeout_seconds"]}
        for item in parsed.get("artifacts", [])
        if item["kind"] == "python"
    ]


def collect_artifacts(manager: Any) -> dict[str, Any]:
    """Collect the bounded artifacts/ files the Session wrote, with digests and bytes.

    Anything unreadable or over a limit marks the result incomplete instead of raising.
    """
    files: dict[str, Any] = {}
    total = 0
    complete = True
    for path in sorted(manager.list_workspace_files()):
        if not path.startswith("artifacts/"):
            continue
        if len(files) >= MAX_ARTIFACTS:
            complete = False
            break
        try:
            artifact_path(path)
            raw = manager.read_workspace_file(path, max_bytes=MAX_ARTIFACT_BYTES)
            total += len(raw)
            if total > MAX_EVIDENCE_BYTES:
                complete = False
                break
            files[path] = {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()}
        except (ValueError, OSError):
            complete = False
    return {"complete": complete, "files": files}


def _equal(actual: Any, expected: Any, tolerance: float, *, csv_numbers: bool = False) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _equal(actual[key], value, tolerance, csv_numbers=csv_numbers)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_equal(a, b, tolerance, csv_numbers=csv_numbers) for a, b in zip(actual, expected, strict=True))
        )
    if type(expected) in (int, float):
        if isinstance(actual, bool):
            return False
        if not csv_numbers and type(actual) not in (int, float):
            return False
        try:
            return math.isfinite(float(actual)) and abs(float(actual) - expected) <= tolerance
        except (TypeError, ValueError):
            return False
    return type(actual) is type(expected) and actual == expected


def _file(receipt: dict[str, Any]) -> bytes:
    raw = base64.b64decode(receipt["base64"], validate=True) if "base64" in receipt else receipt["text"].encode("utf-8")
    if len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
        raise ValueError("Invalid artifact digest or size")
    return raw


def artifact_correctness(gold: dict[str, Any], evidence: dict[str, Any]) -> tuple[float, str] | None:
    """Score declared artifacts as (score, reason), or return None when none are declared."""
    specifications = validate_structured(gold).get("artifacts", [])
    if not specifications:
        return None
    observed = evidence.get("artifacts", {})
    if observed.get("complete") is not True:
        return 0.0, "Required artifact evidence is missing or incomplete"
    for item in specifications:
        path, kind = item["path"], item["kind"]
        try:
            receipt = observed["files"][path]
            raw = _file(receipt)
            text = raw.decode("utf-8")
            if kind == "text":
                passed = text == item["expected"]
            elif kind == "json":
                passed = _equal(json.loads(text), item["expected"], item["tolerance"])
            elif kind == "csv":
                rows = list(csv.DictReader(io.StringIO(text)))
                keys = item["key_columns"]
                actual = {tuple(row[key] for key in keys): row for row in rows}
                wanted = {tuple(str(row[key]) for key in keys): row for row in item["expected"]}
                passed = (
                    len(actual) == len(rows) and len(wanted) == len(item["expected"]) and set(actual) == set(wanted)
                )
                passed = passed and all(
                    _equal(actual[key], row, item["tolerance"], csv_numbers=True) for key, row in wanted.items()
                )
            else:
                verification = evidence.get("python", {}).get(path, {})
                passed = (
                    verification.get("complete") is True
                    and verification.get("sha256") == receipt["sha256"]
                    and verification.get("exit_code") == item["exit_code"]
                    and (item["stdout"] is None or verification.get("stdout") == item["stdout"])
                )
            if not passed:
                return 0.0, f"Artifact check failed: {path}"
        except (KeyError, TypeError, ValueError, UnicodeError):
            return 0.0, f"Artifact evidence unavailable or invalid: {path}"
    return 1.0, "Every declared artifact passed"


def delegation_correctness(gold: dict[str, Any], evidence: dict[str, Any]) -> tuple[float, str] | None:
    """Score required children as (score, reason), or return None when none are required.

    Each required child must match one unused successful child by inputs, artifacts, and promoted digests.
    """
    required = validate_structured(gold).get("children")
    if required is None:
        return None
    actual = evidence.get("children")
    if evidence.get("children_complete") is not True or not isinstance(actual, list) or len(actual) != len(required):
        return 0.0, "Child count or lifecycle evidence does not match"
    remaining = list(actual)
    files = evidence.get("artifacts", {}).get("files", {})
    for expectation in required:
        found = None
        for index, child in enumerate(remaining):
            if child.get("status") != "succeeded" or set(child.get("inputs", {})) != set(expectation["input_paths"]):
                continue
            receipts = child.get("artifacts", [])
            suffixes = {row["path"].split("/", 2)[-1] for row in receipts}
            if not set(expectation["artifacts"]) <= suffixes:
                continue
            if any(files.get(row.get("root_path", row["path"]), {}).get("sha256") != row["sha256"] for row in receipts):
                continue
            found = index
            break
        if found is None:
            return 0.0, "Child inputs, success, or promoted artifact digest did not match"
        remaining.pop(found)
    return 1.0, "Every declared child and promotion receipt passed"


def parse_expectations_file(path: str) -> dict[str, Any]:
    """Read and validate an expectations JSON file within the size limit."""
    from pathlib import Path

    raw = Path(path).read_bytes()
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("Expectations file exceeds the size limit")
    return validate_structured(json.loads(raw))
