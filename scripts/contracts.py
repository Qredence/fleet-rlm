from __future__ import annotations

# OpenAPI, TUI HTTP types, and API chunk contracts.
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

API_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(API_ROOT / "src"))

API_OUTPUT = API_ROOT / "openapi.yaml"

API_TUI_ROOT = API_ROOT / "tools" / "fleet-tui"

API_TUI_OUTPUT = API_TUI_ROOT / "src" / "generated" / "openapi.ts"


class OpenApiDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: object) -> bool:
        del data
        return True


def api__schema() -> dict[str, Any]:
    from fleet_rlm.main import app

    schema = app.openapi()
    schema["openapi"] = "3.1.0"
    return schema


def api__render(schema: dict[str, Any]) -> str:
    return yaml.dump(
        schema,
        Dumper=OpenApiDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def api_generate(_args: argparse.Namespace) -> int:
    schema = api__schema()
    API_OUTPUT.write_text(api__render(schema), encoding="utf-8")
    API_TUI_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    api__generate_typescript(API_TUI_OUTPUT)
    print(f"Generated {len(schema.get('paths', {}))} backend paths and TUI HTTP types")
    return 0


def api_check(_args: argparse.Namespace) -> int:
    if not API_OUTPUT.exists():
        print(f"Missing generated contract: {API_OUTPUT}", file=sys.stderr)
        return 1
    expected = api__render(api__schema())
    actual = API_OUTPUT.read_text(encoding="utf-8")
    if actual != expected:
        print("openapi.yaml is stale; run `make api-sync`", file=sys.stderr)
        return 1

    schema = yaml.safe_load(actual)
    paths = schema.get("paths", {})
    if any(path.startswith("/api/v1") for path in paths):
        print("Legacy /api/v1 path found in backend contract", file=sys.stderr)
        return 1
    if "post" in paths.get("/api/artifacts", {}) or "post" in paths.get("/api/artifacts/{artifact_id}", {}):
        print("Public Artifact creation must not exist", file=sys.stderr)
        return 1
    if any("/stage" in path for path in paths):
        print("Public Attachment stage must not exist", file=sys.stderr)
        return 1

    if not API_TUI_OUTPUT.exists():
        print("Missing generated TUI HTTP types; run `make api-sync`", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as directory:
        expected_types = Path(directory) / "openapi.ts"
        api__generate_typescript(expected_types)
        if API_TUI_OUTPUT.read_text(encoding="utf-8") != expected_types.read_text(encoding="utf-8"):
            print("TUI HTTP types are stale; run `make api-sync`", file=sys.stderr)
            return 1
    print(f"Backend OpenAPI and TUI HTTP types are current ({len(paths)} paths)")
    return 0


def api__generate_typescript(output: Path) -> None:
    subprocess.run(
        (
            "pnpm",
            "exec",
            "openapi-typescript",
            str(API_OUTPUT),
            "-o",
            str(output),
        ),
        cwd=API_TUI_ROOT,
        check=True,
    )


def api_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate").set_defaults(func=api_generate)
    commands.add_parser("check").set_defaults(func=api_check)
    args = parser.parse_args()
    return args.func(args)


# Deterministic TUI stream fixture contract.


import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

STREAM_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(STREAM_ROOT / "src"))

STREAM_FIXTURE = STREAM_ROOT / "tools" / "fleet-tui" / "src" / "tests" / "fixtures" / "turn-stream.jsonl"

STREAM_SESSION_ID = UUID("00000000-0000-0000-0000-000000000000")

STREAM_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)

STREAM_RUN_IDS = {
    "happy": UUID("11111111-1111-1111-1111-111111111111"),
    "failure": UUID("22222222-2222-2222-2222-222222222222"),
    "abort": UUID("33333333-3333-3333-3333-333333333333"),
}


def stream__project(details: tuple[Any, ...], run_id: UUID) -> list[dict[str, Any]]:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import RuntimeEvent

    projector = AISDKUIProjector()
    chunks: list[dict[str, Any]] = []
    for sequence, detail in enumerate(details, start=1):
        event = RuntimeEvent(
            schema_version=1,
            event_id=uuid5(run_id, str(sequence)),
            run_id=run_id,
            session_id=STREAM_SESSION_ID,
            sequence=sequence,
            timestamp=STREAM_TIMESTAMP,
            detail=detail,
        )
        chunks.extend(projector.project(event))
    return chunks


def stream__happy_details() -> tuple[Any, ...]:
    from fleet_rlm.rlm.events import (
        ArtifactCreated,
        AttachmentRead,
        ChildProgress,
        RLMCode,
        RLMOutput,
        RLMReasoning,
        RunCompleted,
        RunStarted,
        SkillActivated,
        SkillLoaded,
        Status,
        StepFinished,
        StepStarted,
        StructuredResult,
        TextCompleted,
        TextDelta,
        ToolCompleted,
        ToolFailed,
        ToolStarted,
        Usage,
        WarningEvent,
    )

    return (
        RunStarted("live"),
        Status("execution", "running", "preparing skills"),
        ChildProgress(
            child_id="child-1",
            task_label="Inspect selected evidence",
            state="running",
            elapsed_ms=12,
            cleanup_state="pending",
            parent_run_id=str(STREAM_RUN_IDS["happy"]),
        ),
        SkillActivated("skill-inspect", "inspect", "1.0.0", "system", ("read",)),
        SkillLoaded("skill-exec", "exec", "1.2.0"),
        StepStarted(1),
        RLMReasoning("Let me think", step=1, is_delta=True, is_final=False),
        RLMReasoning("Let me think through the steps", step=1, is_delta=True, is_final=True),
        RLMCode("print(1)", step=1),
        RLMOutput("1", step=1),
        StepFinished(1),
        StepStarted(2),
        ToolStarted("tool-1", "shell", {"cmd": "ls"}),
        ToolCompleted("tool-1", "shell", {"exit_code": 0}),
        ToolStarted("tool-2", "shell", {"cmd": "missing"}),
        ToolFailed("tool-2", "shell", "command not found"),
        AttachmentRead(
            UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            "input.txt",
            2,
        ),
        ArtifactCreated(
            UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            "markdown",
            "report",
            "text/markdown",
            3,
            "a" * 64,
        ),
        Usage({"iterations": 2, "observed_lm_usage": {}, "duration_ms": 10}),
        StructuredResult("answer", "1", 7),
        WarningEvent("deprecated option", "warn-1"),
        TextDelta("hello"),
        TextCompleted("hello world"),
        StepFinished(2),
        RunCompleted(1, "live", 42),
    )


def stream__failure_details() -> tuple[Any, ...]:
    from fleet_rlm.rlm.events import RunFailed, RunStarted, TextDelta

    return (
        RunStarted("live"),
        TextDelta("partial answer"),
        RunFailed("execution_failed", "Turn failed"),
    )


def stream__abort_details() -> tuple[Any, ...]:
    from fleet_rlm.rlm.events import RunCancelled, RunStarted, StepStarted

    return (
        RunStarted("live"),
        StepStarted(1),
        RunCancelled(),
    )


def stream_generate_streams() -> list[list[dict[str, Any]]]:
    """Project all streams to chunks. Deterministic for a given projector."""
    return [
        stream__project(stream__happy_details(), STREAM_RUN_IDS["happy"]),
        stream__project(stream__failure_details(), STREAM_RUN_IDS["failure"]),
        stream__project(stream__abort_details(), STREAM_RUN_IDS["abort"]),
    ]


def stream__render(streams: list[list[dict[str, Any]]]) -> str:
    from fastapi.encoders import jsonable_encoder

    # Mirror the wire format exactly: FastAPI's SSE path serializes each chunk
    # as json.dumps(jsonable_encoder(chunk)) (fastapi.routing._serialize_sse_item).
    # This keeps the fixture byte-identical to what the TUI receives.
    lines: list[str] = []
    for stream in streams:
        lines.extend(json.dumps(jsonable_encoder(chunk)) for chunk in stream)
        lines.append("[DONE]")
    return "\n".join(lines) + "\n"


def stream_generate(_args: argparse.Namespace) -> int:
    STREAM_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    STREAM_FIXTURE.write_text(stream__render(stream_generate_streams()), encoding="utf-8")
    print(f"Wrote TUI turn-stream fixture ({len(list(STREAM_FIXTURE.open()))} lines)")
    return 0


def stream_check(_args: argparse.Namespace) -> int:
    if not STREAM_FIXTURE.exists():
        print(f"Missing TUI turn-stream fixture: {STREAM_FIXTURE}", file=sys.stderr)
        return 1
    expected = stream__render(stream_generate_streams())
    actual = STREAM_FIXTURE.read_text(encoding="utf-8")
    if actual != expected:
        print("TUI turn-stream fixture is stale; run `make stream-sync`", file=sys.stderr)
        return 1
    print("TUI turn-stream fixture is current")
    return 0


def stream_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate").set_defaults(func=stream_generate)
    commands.add_parser("check").set_defaults(func=stream_check)
    args = parser.parse_args()
    return args.func(args)


# OpenAPI-derived runtime chunk validation contract.


import sys
from pathlib import Path

import yaml

CHUNKS_ROOT = Path(__file__).resolve().parents[1]

CHUNKS_OPENAPI = CHUNKS_ROOT / "openapi.yaml"

CHUNKS_TARGET = CHUNKS_ROOT / "tools" / "fleet-tui" / "src" / "generated" / "fleet-ui-chunk-validation.ts"

CHUNK_FIELD_ALTERNATIVES: dict[str, tuple[tuple[str, ...], ...]] = {
    "data-status": (("status",), ("detail",), ("message",)),
    "data-skill": (("skill_id",),),
    "data-attachment": (("attachment_id",), ("attachmentId",)),
    "data-artifact": (("artifact_id",),),
    "data-structured-result": (("schema_id", "schema_version"),),
}


def chunks__check_name(schema: dict) -> str | None:
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


def chunks__tables() -> dict[str, list]:
    doc = yaml.safe_load(CHUNKS_OPENAPI.read_text(encoding="utf-8"))
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
            chunks_check = chunks__check_name(schema)
            if chunks_check is not None:
                checks[name] = chunks_check
        field_checks[ctype] = checks
        required[ctype] = list(data.get("required") or ())
    return {"chunk_types": chunk_types, "field_checks": field_checks, "required": required}


def chunks__render(tables: dict) -> str:
    out: list[str] = [
        "/**",
        " * REGENERATED from openapi.yaml by scripts/contracts.py api generate.",
        " * Do not hand-edit — run `make api-sync`. The dataAlternatives",
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
        for name, chunks_check in checks.items():
            out.append(f"    {name}: {chunks_check},")
        out.append("  },")
    out.append("};")
    out.append("")
    out.append("export const dataRequiredFields: Record<string, readonly string[]> = {")
    for ctype, fields in tables["required"].items():
        out.append(f"  {json.dumps(ctype)}: {json.dumps(fields)},")
    out.append("};")
    out.append("")
    out.append("export const dataAlternatives: Record<string, readonly (readonly string[])[]> = {")
    for ctype, groups in CHUNK_FIELD_ALTERNATIVES.items():
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


def chunks_generate(_args: argparse.Namespace) -> int:
    CHUNKS_TARGET.parent.mkdir(parents=True, exist_ok=True)
    CHUNKS_TARGET.write_text(chunks__render(chunks__tables()), encoding="utf-8")
    print(f"Wrote {CHUNKS_TARGET.relative_to(CHUNKS_ROOT)} from {CHUNKS_OPENAPI.name}")
    return 0


def chunks_check(_args: argparse.Namespace) -> int:
    if not CHUNKS_TARGET.exists():
        print(f"Missing generated chunk validation tables: {CHUNKS_TARGET}", file=sys.stderr)
        return 1
    expected = chunks__render(chunks__tables())
    actual = CHUNKS_TARGET.read_text(encoding="utf-8")
    if actual != expected:
        print("TUI chunk-validation tables are stale; run `make api-sync`", file=sys.stderr)
        return 1
    print("TUI chunk-validation tables are current")
    return 0


def chunks_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate").set_defaults(func=chunks_generate)
    commands.add_parser("check").set_defaults(func=chunks_check)
    args = parser.parse_args()
    return args.func(args)


# TOML-derived runtime configuration environment reference.


import sys
from pathlib import Path

from fleet_rlm.config.loader import (
    load_configuration_environment_contract,
)

CONFIG_REPO_ROOT = Path(__file__).resolve().parents[1]

CONFIG_OUTPUT = CONFIG_REPO_ROOT / "docs" / "reference" / "configuration-environment.md"

CONFIG_HEADER = "<!-- Generated by uv run python scripts/contracts.py config generate. Do not edit manually. -->"


def config__names(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) or "—"


def config_render() -> str:
    """Render the environment reference from the committed single policy."""
    contract = load_configuration_environment_contract()
    rows = [
        CONFIG_HEADER,
        "# Configuration environment reference",
        "",
        "Generated from `config/fleet.toml`, the source of truth for the single runtime configuration.",
        "",
        "| Setting | Configured value |",
        "| --- | --- |",
        f"| Runtime | `{contract.runtime_environment}` |",
        f"| Provider | {contract.provider} |",
        f"| Root model | `{contract.root_model}` |",
        f"| Sub model | `{contract.sub_model}` |",
        f"| Root / Sub max tokens | {contract.root_max_tokens or 'default'} / {contract.sub_max_tokens or 'default'} |",
        f"| Fleet child recursion | {'enabled' if contract.recursion_enabled else 'disabled'} |",
        f"| MLflow | {contract.mlflow_tracking_uri if contract.mlflow_tracing_enabled else 'disabled'} |",
        f"| Provider environment names | {config__names(contract.provider_environment_names)} |",
        "| Database environment name | "
        f"{config__names((contract.database_url_env,) if contract.database_url_env else ())} |",
        f"| Snapshot environment names | {config__names(contract.daytona_snapshot_environment_names)} |",
        f"| Optional MLflow environment names | {config__names(contract.mlflow_environment_names)} |",
        "",
        "Startup requires a configured database URL at Alembic head. Local SQLite and PostgreSQL are supported; "
        "the explicit Lakebase preflight additionally enforces its TLS and role requirements.",
        "",
        "Live verification requires operator authorization and the configured provider credentials. "
        "Native-only checks require `rlm.recursion_enabled = false`; recursive checks require it enabled. "
        "Checks never select another configuration. Isolated proofs use temporary database URLs as documented.",
        "",
    ]
    return "\n".join(rows)


def config_build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument("--output", type=Path, default=CONFIG_OUTPUT)
    return parser


def config_main(argv: list[str] | None = None) -> int:
    args = config_build_parser().parse_args(argv)
    output = args.output.resolve()
    rendered = config_render()
    if args.command == "generate":
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(f"generated {output}")
        return 0
    if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
        print(f"configuration reference is stale: {output}", file=sys.stderr)
        return 1
    print(f"configuration reference is current: {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="contract", required=True)
    api = commands.add_parser("api", help="Generate or check OpenAPI, TUI HTTP types, and chunk tables")
    api.add_argument("action", choices=("generate", "check"))
    stream = commands.add_parser("stream", help="Generate or check the deterministic TUI stream fixture")
    stream.add_argument("action", choices=("generate", "check"))
    config = commands.add_parser("config", help="Generate or check the TOML-derived environment reference")
    config.add_argument("action", choices=("generate", "check"))
    config.add_argument("--output", type=Path, default=CONFIG_OUTPUT)
    args = parser.parse_args(argv)
    if args.contract == "api":
        first = api_generate(args) if args.action == "generate" else api_check(args)
        if first:
            return first
        return chunks_generate(args) if args.action == "generate" else chunks_check(args)
    if args.contract == "stream":
        return stream_generate(args) if args.action == "generate" else stream_check(args)
    command_args = [args.action, "--output", str(args.output)]
    return config_main(command_args)


if __name__ == "__main__":
    raise SystemExit(main())
