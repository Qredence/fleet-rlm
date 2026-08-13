#!/usr/bin/env python3
"""Generate the TUI runtime chunk-validation tables from openapi.yaml.

The AI SDK UI chunk contract is typed in ``src/fleet_rlm/api/ui_stream.py``
and adapted by ``src/fleet_rlm/api/sse.py`` and
``src/fleet_rlm/api/openapi.py``. This script derives the
third surface — the TUI's strictest runtime validator — from the OpenAPI
``FleetUIMessageChunk`` variants, so the strict consumer can no longer drift
silently from the documented schema. The dual snake_case/camelCase id
tolerances stay explicit as `_FIELD_ALTERNATIVES` below (they are a
deliberate wire compatibility surface, not schema facts).

Run ``make api-sync`` after changing the OpenAPI hook; ``make api-check`` fails
when ``tools/fleet-tui/src/generated/fleet-ui-chunk-validation.ts`` is stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "openapi.yaml"
TARGET = ROOT / "tools" / "fleet-tui" / "src" / "generated" / "fleet-ui-chunk-validation.ts"

# Dual snake_case/camelCase id tolerances (hand-maintained contract; at least
# one form per group must be present for the payload to validate).
_FIELD_ALTERNATIVES: dict[str, tuple[tuple[str, ...], ...]] = {
    "data-status": (("status",), ("detail",), ("message",)),
    "data-skill": (("skill_id",), ("skillId",)),
    "data-attachment": (("attachment_id",), ("attachmentId",)),
    "data-artifact": (("artifact_id",), ("artifactId",)),
    "data-structured-result": (("schema_id", "schema_version"), ("schemaId", "schemaVersion")),
}


def _check_name(schema: dict) -> str | None:
    """Map one OpenAPI property schema to a FieldCheck expression (or None to omit)."""
    schema = {key: value for key, value in schema.items() if key not in {"title", "default"}}
    if "anyOf" in schema:
        parts = schema["anyOf"]
        if len(parts) == 2 and parts[1] == {"type": "null"}:
            base = parts[0]
            if base.get("type") == "string":
                return "isNullableString"
            if base.get("type") == "integer":
                return "isNullableInteger"
            if base.get("type") == "boolean":
                return "isNullableBoolean"
            if base.get("type") == "array" and base.get("items", {}).get("type") == "string":
                return "isNullableStringArray"
        raise SystemExit(f"unsupported anyOf schema: {json.dumps(schema)}")
    stype = schema.get("type")
    enum = schema.get("enum")
    if enum and stype == "string":
        quoted = " || ".join(f"value === {json.dumps(item)}" for item in enum)
        return f"(value) => {quoted}"
    match stype:
        case "string":
            return "isString"
        case "integer":
            return "isInteger"
        case "boolean":
            return "isBoolean"
        case "array" if schema.get("items", {}).get("type") == "string":
            return "isStringArray"
        case "object":
            return "isRecord"
        case None if schema.get("type") is None and not schema:
            return None  # untyped `{}` payloads (e.g. structured-result value) skip the check
    raise SystemExit(f"unsupported schema: {json.dumps(schema)}")


def _tables() -> dict[str, list]:
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    variants = doc["components"]["schemas"]["FleetUIMessageChunk"]["oneOf"]
    chunk_types: list[str] = []
    field_checks: dict[str, dict[str, str]] = {}
    required: dict[str, list[str]] = {}
    for variant in variants:
        props = variant.get("properties", {})
        ctype = props["type"]["const"]
        chunk_types.append(ctype)
        data = props.get("data") or {}
        data_props = data.get("properties") or {}
        if not data_props:
            continue
        checks: dict[str, str] = {}
        for name, schema in data_props.items():
            check = _check_name(schema)
            if check is not None:
                checks[name] = check
        field_checks[ctype] = checks
        required[ctype] = list(data.get("required") or ())
    return {"chunk_types": chunk_types, "field_checks": field_checks, "required": required}


def _render(tables: dict) -> str:
    out: list[str] = [
        "/**",
        " * REGENERATED from openapi.yaml by scripts/generate_tui_chunk_validation.py.",
        " * Do not hand-edit — run `make api-sync`. The dataAlternatives dual",
        " * snake_case/camelCase id tolerances are the generator's declared input.",
        " */",
        "",
        "export type FieldCheck = (value: unknown) => boolean;",
        "",
        f"export const chunkTypes = {json.dumps(tables['chunk_types'], indent=2)} as const;",
        "",
        "export const dataFieldChecks: Record<string, Record<string, FieldCheck>> = {",
    ]
    for ctype, checks in tables["field_checks"].items():
        out.append(f"  {json.dumps(ctype)}: {{")
        for name, check in checks.items():
            out.append(f"    {name}: {check},")
        out.append("  },")
    out.append("};")
    out.append("")
    out.append("export const dataRequiredFields: Record<string, readonly string[]> = {")
    for ctype, fields in tables["required"].items():
        out.append(f"  {json.dumps(ctype)}: {json.dumps(fields)},")
    out.append("};")
    out.append("")
    out.append("export const dataAlternatives: Record<string, readonly (readonly string[])[]> = {")
    for ctype, groups in _FIELD_ALTERNATIVES.items():
        rendered = "[" + ", ".join("[" + ", ".join(json.dumps(g) for g in group) + "]" for group in groups) + "]"
        out.append(f"  {json.dumps(ctype)}: {rendered},")
    out.append("};")
    out.append("")
    out.append(
        """function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNullableString(value: unknown): boolean {
  return value === null || isString(value);
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isNullableBoolean(value: unknown): boolean {
  return value === null || isBoolean(value);
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

function isNullableInteger(value: unknown): boolean {
  return value === null || isInteger(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isString);
}

function isNullableStringArray(value: unknown): boolean {
  return value === null || isStringArray(value);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
""",
    )
    return "\n".join(out) + "\n"


def generate(_args: argparse.Namespace) -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(_render(_tables()), encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(ROOT)} from {OPENAPI.name}")
    return 0


def check(_args: argparse.Namespace) -> int:
    if not TARGET.exists():
        print(f"Missing generated chunk validation tables: {TARGET}", file=sys.stderr)
        return 1
    expected = _render(_tables())
    actual = TARGET.read_text(encoding="utf-8")
    if actual != expected:
        print("TUI chunk-validation tables are stale; run `make api-sync`", file=sys.stderr)
        return 1
    print("TUI chunk-validation tables are current")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate").set_defaults(func=generate)
    commands.add_parser("check").set_defaults(func=check)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
