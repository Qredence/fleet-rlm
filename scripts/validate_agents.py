"""Validate .claude/agents/*.md files against Claude Code subagent spec."""

from __future__ import annotations

import pathlib
import sys

import yaml

VALID_TOOLS = {"Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task"}
VALID_MODELS = {"sonnet", "opus", "haiku", "inherit"}
VALID_PERM_MODES = {
    "default",
    "acceptEdits",
    "delegate",
    "dontAsk",
    "bypassPermissions",
    "plan",
}
REQUIRED_FIELDS = {"name", "description"}
OPTIONAL_FIELDS = {
    "tools",
    "disallowedTools",
    "model",
    "permissionMode",
    "maxTurns",
    "skills",
    "mcpServers",
    "hooks",
    "memory",
}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


def validate_agent(path: pathlib.Path) -> list[str]:
    errors: list[str] = []
    content = path.read_text()

    if not content.startswith("---"):
        return [f"{path.name}: Missing YAML frontmatter"]

    parts = content.split("---", 2)
    if len(parts) < 3:
        return [f"{path.name}: Malformed frontmatter"]

    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"]

    if not isinstance(fm, dict):
        return [f"{path.name}: Frontmatter is not a mapping"]

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f'{path.name}: Missing required field "{field}"')

    # Unknown fields
    for key in fm:
        if key not in ALL_FIELDS:
            errors.append(f'{path.name}: Unknown frontmatter field "{key}"')

    # Model
    if "model" in fm and fm["model"] not in VALID_MODELS:
        errors.append(f'{path.name}: Invalid model "{fm["model"]}"')

    # Tools
    if "tools" in fm:
        tools_raw = fm["tools"]
        if isinstance(tools_raw, str):
            tools_list = [t.strip() for t in tools_raw.split(",")]
        elif isinstance(tools_raw, list):
            tools_list = [str(t) for t in tools_raw]
        else:
            tools_list = []
            errors.append(f"{path.name}: tools must be string or list")
        for t in tools_list:
            base = t.split("(")[0].strip()
            if base not in VALID_TOOLS:
                errors.append(f'{path.name}: Unknown tool "{t}" (base: "{base}")')

    # Permission mode
    if "permissionMode" in fm and fm["permissionMode"] not in VALID_PERM_MODES:
        errors.append(f'{path.name}: Invalid permissionMode "{fm["permissionMode"]}"')

    # Memory
    if "memory" in fm and fm["memory"] not in {"user", "project", "local"}:
        errors.append(f'{path.name}: Invalid memory scope "{fm["memory"]}"')

    # Body line count
    body = parts[2]
    line_count = len(body.strip().splitlines())

    status = "✗" if errors else "✓"
    print(f"{status} {path.name}")
    print(f"  name: {fm.get('name', 'MISSING')}")
    print(f"  model: {fm.get('model', '(not set - inherits)')}")
    print(f"  tools: {fm.get('tools', '(inherits all)')}")
    print(f"  skills: {fm.get('skills', '(none)')}")
    print(f"  maxTurns: {fm.get('maxTurns', '(not set)')}")
    print(f"  memory: {fm.get('memory', '(not set)')}")
    print(f"  body lines: {line_count}")
    print()

    return errors


def main() -> None:
    agents_dir = pathlib.Path(".claude/agents")
    if not agents_dir.exists():
        print("No .claude/agents/ directory found")
        sys.exit(1)

    all_errors: list[str] = []
    for f in sorted(agents_dir.glob("*.md")):
        all_errors.extend(validate_agent(f))

    if all_errors:
        print("ERRORS:")
        for e in all_errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("All agents pass validation! ✓")


if __name__ == "__main__":
    main()
