"""SUBMIT / FinalOutput helpers for clean Daytona interpreter binding."""

from __future__ import annotations

import json
from typing import Any

_FINAL_OUTPUT_MARKER = "__FLEET_CLEAN_FINAL_OUTPUT__"


class FleetFinalOutput(Exception):
    """Raised inside the interpreter when SUBMIT completes successfully."""

    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        super().__init__("Final output submitted")


def build_submit_setup_code(output_fields: list[dict[str, Any]] | None) -> str:
    """Inject typed or generic SUBMIT into a Python interpreter namespace.

    Expects ``FleetFinalOutput`` and ``_FINAL_OUTPUT_MARKER`` already bound
    in the target namespace (in-process) or defined by the prelude for remote.
    """
    typed = _typed_submit_source(output_fields) if output_fields else _generic_submit_source()
    return typed


def remote_submit_setup_code(output_fields: list[dict[str, Any]] | None) -> str:
    """Full setup string for remote Daytona REPL (defines exception + SUBMIT)."""
    return f"""
import json as _json
_FINAL_OUTPUT_MARKER = {_FINAL_OUTPUT_MARKER!r}

class FleetFinalOutput(Exception):
    def __init__(self, value):
        self.value = value
        super().__init__("Final output submitted")

{build_submit_setup_code(output_fields)}
""".strip()


def _generic_submit_source() -> str:
    return """
def SUBMIT(**kwargs):
    print(f"{_FINAL_OUTPUT_MARKER}{_json.dumps(kwargs, ensure_ascii=False)}{_FINAL_OUTPUT_MARKER}")
    raise FleetFinalOutput(kwargs)
""".strip()


def _typed_submit_source(output_fields: list[dict[str, Any]]) -> str:
    sig_parts: list[str] = []
    dict_parts: list[str] = []
    for field in output_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        part = name
        type_hint = str(field.get("type") or "").strip()
        if type_hint:
            part += f": {type_hint}"
        sig_parts.append(part)
        dict_parts.append(f'"{name}": {name}')
    signature = ", ".join(sig_parts) or "**kwargs"
    if dict_parts:
        body = f"result = {{{', '.join(dict_parts)}}}"
    else:
        body = "result = dict(kwargs)"
    return f"""
def SUBMIT({signature}):
    {body}
    print(f"{{_FINAL_OUTPUT_MARKER}}{{_json.dumps(result, ensure_ascii=False)}}{{_FINAL_OUTPUT_MARKER}}")
    raise FleetFinalOutput(result)
""".strip()


def extract_final_payload(stdout: str, *, marker: str = _FINAL_OUTPUT_MARKER) -> dict[str, Any] | None:
    start = stdout.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = stdout.find(marker, start)
    if end == -1:
        return None
    try:
        parsed = json.loads(stdout[start:end])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
