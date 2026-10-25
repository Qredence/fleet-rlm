"""Bounded, host-collected execution evidence and the gold schema it is scored against.

Gold is never a program input. This module observes files and lifecycle receipts and
validates reviewed expectations; `optimize/objectives.py` compares the two.
"""

from __future__ import annotations

import base64
import hashlib
import json
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


def parse_expectations_file(path: str) -> dict[str, Any]:
    """Read and validate an expectations JSON file within the size limit."""
    from pathlib import Path

    raw = Path(path).read_bytes()
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("Expectations file exceeds the size limit")
    return validate_structured(json.loads(raw))
