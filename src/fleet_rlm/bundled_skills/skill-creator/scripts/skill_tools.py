"""Validate and scaffold Fleet user Skills compatible with save_skill and .fleet/skills/."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _bundled_skill_names() -> frozenset[str]:
    """Reserved names the sandbox preamble exports as FLEET_BUNDLED_SKILLS, else this package's bundle."""
    injected = os.environ.get("FLEET_BUNDLED_SKILLS")
    if injected is not None:
        return frozenset(name for name in injected.split(",") if name)
    bundle = Path(__file__).resolve().parents[2]
    return frozenset(child.name for child in bundle.iterdir() if child.is_dir() and (child / "SKILL.md").is_file())


BUNDLED_SKILL_NAMES = _bundled_skill_names()
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_NAME_LEN = 64
_MAX_DESC_LEN = 1024
_MAX_RESOURCE_FILES = 20
_MAX_FILE_BYTES = 200_000
_MAX_TOTAL_BYTES = 2_000_000
_MAX_PRELOADED_CHARS = 20_000


def _validate_name_and_description(name: str, description: str) -> tuple[str, str]:
    """Validate skill folder name and single-line description, rejecting bundled names."""
    if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name) or len(name) > _MAX_NAME_LEN:
        raise ValueError("Skill name must be 1-64 lowercase alphanumeric words separated by hyphens")
    if name in BUNDLED_SKILL_NAMES:
        raise ValueError(f"Cannot overwrite bundled skill {name!r}; choose a new skill name")
    if not isinstance(description, str) or not description.strip() or len(description) > _MAX_DESC_LEN:
        raise ValueError("Skill description must contain 1-1024 characters")
    if "\x00" in description or "</preloaded_skill>" in description or "\n" in description or "\r" in description:
        raise ValueError("Skill description must be a single line without NUL or </preloaded_skill>")
    return name, description.strip()


def validate_skill_spec(
    name: str,
    description: str,
    body: str,
    *,
    scripts: Mapping[str, str] | None = None,
    references: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate skill metadata, Markdown body, script syntax, and size limits."""
    clean_name, clean_desc = _validate_name_and_description(name, description)
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Skill instructions body must be a non-empty string")
    if "\x00" in body or "</preloaded_skill>" in body:
        raise ValueError("Skill instructions must not contain NUL or </preloaded_skill>")
    body_text = body.strip()
    body_bytes = len(body_text.encode("utf-8"))
    if body_bytes > _MAX_FILE_BYTES:
        raise ValueError(f"Skill instructions exceed {_MAX_FILE_BYTES} bytes")

    if scripts is not None and not isinstance(scripts, Mapping):
        raise ValueError("scripts must be a mapping of filename to content")
    if references is not None and not isinstance(references, Mapping):
        raise ValueError("references must be a mapping of filename to content")

    resource_bytes = 0
    script_names: list[str] = []
    reference_names: list[str] = []

    for folder, mapping, names_out in (
        ("scripts", scripts or {}, script_names),
        ("references", references or {}, reference_names),
    ):
        if len(mapping) > _MAX_RESOURCE_FILES:
            raise ValueError(f"{folder}/ may contain at most {_MAX_RESOURCE_FILES} files")
        seen: set[str] = set()
        for raw_filename, content in mapping.items():
            filename = str(raw_filename)
            if (
                not filename
                or Path(filename).name != filename
                or filename in {".", ".."}
                or "/" in filename
                or "\\" in filename
                or "\x00" in filename
                or filename in seen
                or not isinstance(content, str)
            ):
                raise ValueError(f"Invalid {folder} filename or content: {filename!r}")
            seen.add(filename)
            size = len(content.encode("utf-8"))
            if size > _MAX_FILE_BYTES:
                raise ValueError(f"Resource {folder}/{filename} exceeds {_MAX_FILE_BYTES} bytes")
            if folder == "scripts":
                suffix = Path(filename).suffix
                if suffix not in {".py", ".sh"}:
                    raise ValueError(f"Skill script {filename!r} must have .py or .sh extension")
                if suffix == ".py":
                    try:
                        ast.parse(content, filename=filename)
                    except SyntaxError as exc:
                        raise ValueError(f"Syntax error in scripts/{filename}: {exc.msg} (line {exc.lineno})") from exc
            resource_bytes += size
            names_out.append(filename)

    total_bytes = body_bytes + resource_bytes
    if total_bytes > _MAX_TOTAL_BYTES:
        raise ValueError(f"Skill total resources ({total_bytes} bytes) exceed {_MAX_TOTAL_BYTES} bytes")

    skill_md = f"---\nname: {clean_name}\ndescription: {json.dumps(clean_desc)}\n---\n\n{body_text}\n"
    warnings: list[str] = []
    if len(skill_md) > _MAX_PRELOADED_CHARS:
        warnings.append(f"SKILL.md is {len(skill_md)} chars, exceeding the {_MAX_PRELOADED_CHARS}-char preload cap")

    return {
        "valid": True,
        "name": clean_name,
        "description": clean_desc,
        "body": body_text,
        "skill_md": skill_md,
        "script_names": sorted(script_names),
        "reference_names": sorted(reference_names),
        "total_bytes": total_bytes,
        "warnings": warnings,
    }


def _default_script_template(name: str, module_ident: str) -> str:
    """Generate a dual-mode Python script template for a new user skill."""
    return (
        f'"""Executable helper module for the {name} skill."""\n\n'
        "from __future__ import annotations\n\n"
        "import argparse\n"
        "import json\n"
        "import sys\n"
        "from typing import Any\n\n\n"
        "def run(target: str) -> dict[str, Any]:\n"
        f'    """Execute the primary {module_ident} workflow on target and return structured results."""\n'
        '    return {"target": target, "status": "ok"}\n\n\n'
        "def main(argv: list[str] | None = None) -> int:\n"
        f'    """CLI entrypoint for {name}."""\n'
        f'    parser = argparse.ArgumentParser(description="Run {name} helper.")\n'
        '    parser.add_argument("target", help="Workspace file or input target")\n'
        "    args = parser.parse_args(argv)\n"
        "    result = run(args.target)\n"
        '    sys.stdout.write(json.dumps(result, indent=2) + "\\n")\n'
        "    return 0\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )


def scaffold_skill(
    name: str,
    description: str,
    *,
    workflow_steps: Sequence[str] | None = None,
    include_script: bool = True,
    include_reference: bool = False,
) -> dict[str, Any]:
    """Create a validated skill specification with SKILL.md and optional script/reference files."""
    clean_name, clean_desc = _validate_name_and_description(name, description)
    title = clean_name.replace("-", " ").title()
    module_ident = clean_name.replace("-", "_")
    script_name = f"{module_ident}.py"

    steps = list(workflow_steps) if workflow_steps else []
    if not steps:
        steps = [
            "Inspect the relevant files under `/workspace/files/` or `artifacts/` before modifying state.",
            (
                f'Load the helper module via `helper = load_skill_module("{clean_name}")` and run it in Python.'
                if include_script
                else "Perform the computation in Python using standard-library or snapshot-installed packages."
            ),
            "Write outputs under `artifacts/`, print key figures/results, and verify before calling `SUBMIT`.",
        ]

    lines = [f"# {title}", "", clean_desc, "", "## Steps", ""]
    for idx, step in enumerate(steps, 1):
        lines.append(f"{idx}. {str(step).strip()}")
    if include_script:
        lines.extend(
            [
                "",
                "## Helper usage",
                "",
                "```python",
                f'mod = load_skill_module("{clean_name}")',
                'result = mod("files/input.txt")',
                "print(result)",
                "```",
            ]
        )
    if include_reference:
        lines.extend(
            [
                "",
                f'Read `read_skill_reference("{clean_name}", "references/guide.md")` for edge cases and checks.',
            ]
        )
    body = "\n".join(lines)

    scripts = {script_name: _default_script_template(clean_name, module_ident)} if include_script else {}
    references = (
        {"guide.md": f"# {title} Reference\n\nDocument domain formulas, edge cases, and verification checks here.\n"}
        if include_reference
        else {}
    )
    validation = validate_skill_spec(
        clean_name,
        clean_desc,
        body,
        scripts=scripts or None,
        references=references or None,
    )
    return {
        "name": validation["name"],
        "description": validation["description"],
        "body": validation["body"],
        "skill_md": validation["skill_md"],
        "scripts": scripts,
        "references": references,
        "validation": validation,
    }


def run(
    name: str,
    description: str,
    body: str = "",
    *,
    scripts: Mapping[str, str] | None = None,
    references: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate a complete skill spec, or scaffold one when body is empty."""
    if isinstance(body, str) and body.strip():
        return validate_skill_spec(name, description, body, scripts=scripts, references=references)
    scaffold = scaffold_skill(
        name,
        description,
        include_script=scripts is None,
        include_reference=False,
    )
    if scripts is not None or references is not None:
        final_scripts = dict(scripts or {})
        final_refs = dict(references or {})
        validation = validate_skill_spec(
            name,
            description,
            scaffold["body"],
            scripts=final_scripts or None,
            references=final_refs or None,
        )
        return {
            **scaffold,
            "scripts": final_scripts,
            "references": final_refs,
            "validation": validation,
        }
    return scaffold


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for scaffolding or validating a Fleet user Skill."""
    parser = argparse.ArgumentParser(description="Validate or scaffold a Fleet user Skill.")
    parser.add_argument("--name", required=True, help="New kebab-case skill name (not a bundled skill)")
    parser.add_argument("--description", required=True, help="Single-line routing description (1-1024 chars)")
    parser.add_argument("--body", default="", help="Optional SKILL.md instruction body to validate")
    parser.add_argument("--body-file", default=None, help="Optional path to Markdown body file to validate")
    parser.add_argument("--step", action="append", default=[], help="Workflow step when scaffolding (repeatable)")
    parser.add_argument("--no-script", action="store_true", help="Omit scripts/ template when scaffolding")
    parser.add_argument("--include-reference", action="store_true", help="Include references/guide.md template")
    args = parser.parse_args(argv)

    try:
        body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
        if body.strip():
            result: dict[str, Any] = validate_skill_spec(args.name, args.description, body)
        else:
            result = scaffold_skill(
                args.name,
                args.description,
                workflow_steps=args.step or None,
                include_script=not args.no_script,
                include_reference=args.include_reference,
            )
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
