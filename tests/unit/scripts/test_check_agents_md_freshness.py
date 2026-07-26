from __future__ import annotations

from pathlib import Path

from scripts.check_agents_md_freshness import AgentsMdValidator


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_root_map_must_reference_every_discovered_subguide(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "# Root\n\n- `src/package/AGENTS.md`\n")
    _write(
        tmp_path / "src/package/AGENTS.md",
        "# Package\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )
    _write(
        tmp_path / "tools/client/AGENTS.md",
        "# Client\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )
    _write(tmp_path / "Makefile", "check:\n\t@true\n")

    errors = AgentsMdValidator(tmp_path).validate_all()

    assert any(
        error.file == "AGENTS.md"
        and error.issue == "missing_cross_reference"
        and "tools/client/AGENTS.md" in error.detail
        for error in errors
    )


def test_complete_root_map_passes_cross_reference_validation(tmp_path: Path) -> None:
    _write(
        tmp_path / "AGENTS.md",
        "# Root\n\n- `src/package/AGENTS.md`\n- `tools/client/AGENTS.md`\n",
    )
    _write(
        tmp_path / "src/package/AGENTS.md",
        "# Package\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )
    _write(
        tmp_path / "tools/client/AGENTS.md",
        "# Client\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )
    _write(tmp_path / "Makefile", "check:\n\t@true\n")

    errors = AgentsMdValidator(tmp_path).validate_all()

    assert errors == []
