"""Extract a version's release notes from CHANGELOG.md without project imports."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def extract_changelog_notes(version: str, changelog_path: Path) -> str:
    """Return one version's changelog body, without its top-level heading."""
    lines = changelog_path.read_text().splitlines()
    heading = re.compile(rf"^## \[{re.escape(version)}\](?: - .*)?$")
    starts = [index for index, line in enumerate(lines) if heading.fullmatch(line)]
    if not starts:
        raise SystemExit(f"CHANGELOG.md has no section for version {version}")
    if len(starts) != 1:
        raise SystemExit(f"CHANGELOG.md has multiple sections for version {version}")

    start = starts[0] + 1
    end = next((index for index in range(start, len(lines)) if re.match(r"^##(?:\s|$)", lines[index])), len(lines))
    notes = "\n".join(lines[start:end]).strip()
    if not notes:
        raise SystemExit(f"CHANGELOG.md section for version {version} is empty")
    return f"{notes}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(extract_changelog_notes(args.version, args.changelog))


if __name__ == "__main__":
    main()
