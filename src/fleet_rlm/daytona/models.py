"""Models and interfaces for direct Daytona SDK integration."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

FINAL_OUTPUT_MARKER = "__FLEET_FINAL_OUTPUT__"


class FleetFinalOutputError(Exception):
    """Raised inside executed code or tests when final output is submitted."""

    def __init__(self, value: Any) -> None:
        self.value = value
        super().__init__("Final output submitted")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Outcome of a code execution in a Daytona sandbox."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    final_output: dict[str, Any] | None = None
    error: str | None = None


class DaytonaExecutionBackend(Protocol):
    """Contract for executing code and managing files in a Daytona sandbox."""

    async def execute_code(
        self,
        code: str,
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute python code in Daytona sandbox and extract stdout, stderr, exit_code, and SUBMIT output."""
        ...

    async def read_file(self, path: str) -> bytes:
        """Read a file from the sandbox filesystem."""
        ...

    async def write_file(self, path: str, data: bytes) -> None:
        """Write a file to the sandbox filesystem."""
        ...

    async def delete_sandbox(self) -> None:
        """Destroy the underlying sandbox."""
        ...


def validate_json_value(value: Any, *, path: str = "value") -> None:
    """Validate that a value consists only of JSON-serializable types."""
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not __import__("math").isfinite(value):
            raise TypeError(f"{path} contains a non-finite number")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"{path} contains non-string dict key: {k!r}")
            validate_json_value(v, path=f"{path}.{k}")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            validate_json_value(item, path=f"{path}[{i}]")
        return
    raise TypeError(f"{path} contains unsupported type: {type(value).__name__}")


def final_output_frame(value: Mapping[str, Any], *, marker: str = FINAL_OUTPUT_MARKER) -> str:
    """Return the exact private stdout frame emitted by ``SUBMIT``."""
    validate_json_value(value, path="SUBMIT")
    encoded = base64.b64encode(json.dumps(dict(value), ensure_ascii=False, allow_nan=False).encode("utf-8")).decode(
        "ascii"
    )
    return f"{marker}{encoded}{marker}"


def extract_final_payload(stdout: str, *, marker: str = FINAL_OUTPUT_MARKER) -> dict[str, Any] | None:
    """Extract and parse the SUBMIT JSON payload from stdout, if present."""
    if not stdout or marker not in stdout:
        return None

    # First attempt: scan for valid base64-framed markers directly using regex.
    # Base64 strings only contain [A-Za-z0-9+/=] and no underscores.
    pattern = rf"{re.escape(marker)}([A-Za-z0-9+/=]+){re.escape(marker)}"
    matches = re.findall(pattern, stdout)
    for encoded in reversed(matches):
        try:
            payload = base64.b64decode(encoded, validate=True).decode("utf-8")
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    # Fallback attempt: scan adjacent marker pairs in reverse for unencoded or raw JSON payloads.
    marker_len = len(marker)
    positions: list[int] = []
    pos = 0
    while True:
        idx = stdout.find(marker, pos)
        if idx == -1:
            break
        positions.append(idx)
        pos = idx + marker_len

    for i in range(len(positions) - 2, -1, -1):
        start = positions[i] + marker_len
        end = positions[i + 1]
        encoded = stdout[start:end].strip()
        if not encoded:
            continue
        try:
            try:
                payload = base64.b64decode(encoded, validate=True).decode("utf-8")
            except (ValueError, UnicodeError):
                payload = encoded
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    return None


def _generic_submit_source() -> str:
    return f"""
import base64 as _base64
import json as _json

def SUBMIT(**kwargs):
    _fleet_validate_json(kwargs)
    payload = _base64.b64encode(
        _json.dumps(kwargs, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).decode("ascii")
    print(f"{FINAL_OUTPUT_MARKER}{{payload}}{FINAL_OUTPUT_MARKER}", flush=True)
    raise FleetFinalOutputError(kwargs)
""".strip()


def _typed_submit_source(output_fields: list[dict[str, Any]]) -> str:
    """Generate source code for a typed SUBMIT function based on configured output fields."""
    signature_parts: list[str] = []
    validation_parts: list[str] = []
    result_parts: list[str] = []
    default_values: dict[str, str] = {}
    ordered_fields = [
        *[field for field in output_fields if bool(field.get("required", True))],
        *[field for field in output_fields if not bool(field.get("required", True))],
    ]
    for field in ordered_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        type_hint = str(field.get("type") or "").strip()
        required = bool(field.get("required", True))
        parameter = f"{name}: {type_hint}" if type_hint else name
        if not required:
            parameter += "=_FLEET_MISSING"
            default_json = field.get("default_json")
            if not isinstance(default_json, str):
                raise ValueError(f"typed output default for {name} is not JSON-compatible")
            default_values[name] = default_json
        signature_parts.append(parameter)
        if not required:
            validation_parts.extend(
                (
                    f"if {name} is _FLEET_MISSING:",
                    f"    {name} = _fleet_default({name!r})",
                )
            )
        if type_hint in {"str", "builtins.str"}:
            message = (
                f"SUBMIT field {name} must be a string; serialize mappings/lists with "
                "json.dumps(value, ensure_ascii=False)"
            )
            validation_parts.extend(
                (
                    f"if not isinstance({name}, str):",
                    f"    raise TypeError({message!r})",
                )
            )
        result_parts.append(f'"{name}": {name}')
    signature = ", ".join(signature_parts) or "**kwargs"
    body_lines = [
        *validation_parts,
        f"result = {{{', '.join(result_parts)}}}" if result_parts else "result = dict(kwargs)",
    ]
    body = "\n    ".join(body_lines)
    defaults = repr(default_values)
    return f"""
import base64 as _base64
import json as _json

_FLEET_MISSING = object()
_FLEET_DEFAULTS = {defaults}

def _fleet_default(name):
    return _json.loads(_FLEET_DEFAULTS[name])

def SUBMIT({signature}):
    {body}
    _fleet_validate_json(result)
    payload = _base64.b64encode(
        _json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).decode("ascii")
    print(f"{FINAL_OUTPUT_MARKER}{{payload}}{FINAL_OUTPUT_MARKER}", flush=True)
    raise FleetFinalOutputError(result)
""".strip()


def _strict_submit_helpers_source() -> str:
    """Return private remote helpers for strict JSON submission."""
    return """
def _fleet_validate_json(value):
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not __import__("math").isfinite(value):
            raise TypeError("SUBMIT contains a non-finite number")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("SUBMIT contains a non-string mapping key")
            _fleet_validate_json(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _fleet_validate_json(item)
        return
    raise TypeError(f"SUBMIT contains unsupported type: {type(value).__name__}")
""".strip()


def build_submit_setup_code(output_fields: list[dict[str, Any]] | None = None) -> str:
    """Generate the Python source code defining the SUBMIT function."""
    body = _typed_submit_source(output_fields) if output_fields else _generic_submit_source()
    return f"{_strict_submit_helpers_source()}\n\n{body}"


def remote_submit_setup_code(output_fields: list[dict[str, Any]] | None = None) -> str:
    """Generate remote setup source code for submitting final tool output."""
    return f"""
import base64 as _base64
import json
_json = json
FINAL_OUTPUT_MARKER = {FINAL_OUTPUT_MARKER!r}

class FleetFinalOutputError(Exception):
    def __init__(self, value):
        self.value = value
        super().__init__("Final output submitted")

{build_submit_setup_code(output_fields)}
""".strip()
