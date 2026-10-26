"""Tests for changelog-derived release notes."""

from __future__ import annotations

from pathlib import Path
from runpy import run_path

import pytest

ROOT = Path(__file__).resolve().parents[1]
extract_changelog_notes = run_path(str(ROOT / "scripts" / "release_notes.py"))["extract_changelog_notes"]


def test_release_notes_extract_exact_section_without_version_heading(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [0.8.1] - 2026-10-23\n\n### Added\n\n- New feature.\n\n## [0.8.0]\n\n- Previous feature.\n"
    )

    assert extract_changelog_notes("0.8.1", changelog) == "### Added\n\n- New feature.\n"


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("## [0.8.0]\n\n- Existing release.\n", r"has no section for version 0\.8\.1"),
        (
            "## [0.8.1]\n\n- First.\n\n## [0.8.1] - 2026-10-23\n\n- Second.\n",
            r"has multiple sections for version 0\.8\.1",
        ),
        (
            "## [0.8.1] - 2026-10-23\n\n## [0.8.0]\n\n- Previous release.\n",
            r"section for version 0\.8\.1 is empty",
        ),
    ],
    ids=["missing-version", "duplicate-version", "empty-section"],
)
def test_release_notes_reject_invalid_sections(tmp_path: Path, content: str, error: str) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(content)

    with pytest.raises(SystemExit, match=error):
        extract_changelog_notes("0.8.1", changelog)
