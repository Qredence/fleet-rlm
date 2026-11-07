"""Extract a version's release notes from CHANGELOG.md without project imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def extract_changelog_notes(version: str, changelog_path: Path) -> str:
    """Return one version's changelog body, without its top-level heading."""
    return extract_notes(version, changelog_path.read_text())


def extract_notes(version: str, text: str) -> str:
    """Extract notes from a working file or immutable Git blob."""
    lines = text.splitlines()
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


def prepare_notes(version: str, source_sha: str, manifest: Path, directory: Path) -> None:
    """Bind reviewed notes and consumer requirements to the built distributions."""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_sha):
        raise SystemExit("release notes require a full source SHA")
    changelog = subprocess.run(
        ["git", "show", f"{source_sha}:CHANGELOG.md"], check=True, capture_output=True, text=True
    ).stdout
    notes = extract_notes(version, changelog)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "release-notes.md").write_text(notes)
    metadata = {
        "version": version,
        "source_sha": source_sha.lower(),
        "notes_sha256": hashlib.sha256(notes.encode()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "requirements_sha256": hashlib.sha256((directory / "requirements.txt").read_bytes()).hexdigest(),
    }
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def verify_notes(version: str, source_sha: str, manifest: Path, directory: Path) -> dict:
    """Fail closed on changed notes, requirements, source, or distribution identity."""
    metadata = json.loads((directory / "metadata.json").read_text())
    expected = {
        "version": version,
        "source_sha": source_sha.lower(),
        "notes_sha256": hashlib.sha256((directory / "release-notes.md").read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "requirements_sha256": hashlib.sha256((directory / "requirements.txt").read_bytes()).hexdigest(),
    }
    if metadata != expected or not (directory / "release-notes.md").read_text().strip():
        raise SystemExit("release metadata does not match the requested source and artifacts")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path)
    output.add_argument("--prepare", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.prepare:
        if not args.source_sha or not args.manifest:
            parser.error("--prepare requires --source-sha and --manifest")
        prepare_notes(args.version, args.source_sha, args.manifest, args.prepare)
    else:
        args.output.write_text(extract_changelog_notes(args.version, args.changelog))


if __name__ == "__main__":
    main()
