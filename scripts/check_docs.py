#!/usr/bin/env python3
"""Check local Markdown links in product and contributor documentation."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^\s)>]+)>?(?:\s+[^)]*)?\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?", re.MULTILINE)
MARKDOWN_PATHS = (ROOT / "README.md", ROOT / "ARCHITECTURE.md", ROOT / "CONTRIBUTING.md", ROOT / "AGENTS.md")


def markdown_files() -> list[Path]:
    """Return the maintained Markdown files covered by the documentation gate."""
    docs = (path for path in (ROOT / "docs").rglob("*.md") if "plans" not in path.relative_to(ROOT / "docs").parts)
    return sorted(set(MARKDOWN_PATHS).union(docs))


def local_target(source: Path, target: str) -> Path | None:
    """Resolve a local Markdown target, ignoring web links and anchor-only links."""
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return (source.parent / unquote(parsed.path)).resolve()


def check_links() -> list[str]:
    """Return all missing local Markdown targets."""
    failures: list[str] = []
    for source in markdown_files():
        if not source.is_file():
            failures.append(f"Required documentation file is missing: {source.relative_to(ROOT)}")
            continue
        content = source.read_text(encoding="utf-8")
        targets = [match.group(1) for match in MARKDOWN_LINK.finditer(content)]
        targets.extend(match.group(1) for match in REFERENCE_LINK.finditer(content))
        for target in targets:
            resolved = local_target(source, target)
            if resolved is not None and not resolved.exists():
                failures.append(f"{source.relative_to(ROOT)} links to missing path {target!r}")
    return failures


def main() -> int:
    failures = check_links()
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    files = markdown_files()
    print(f"Validated local links in {len(files)} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
