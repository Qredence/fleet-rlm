from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_agents_md_freshness import AgentsMdValidator


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_root_map_must_reference_the_tui_guide(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "# Root\n\n- `tools/client/AGENTS.md`\n")
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
        and "tools/fleet-tui/AGENTS.md" in error.detail
        for error in errors
    )


def test_complete_root_map_passes_cross_reference_validation(tmp_path: Path) -> None:
    _write(
        tmp_path / "AGENTS.md",
        "# Root\n\n- `tools/fleet-tui/AGENTS.md`\n",
    )
    _write(
        tmp_path / "tools/fleet-tui/AGENTS.md",
        "# Package\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )

    _write(tmp_path / "CLAUDE.md", "@AGENTS.md\n")
    _write(tmp_path / AgentsMdValidator.DEVELOPMENT_SKILL, "# Skill\n")
    _write(tmp_path / "ARCHITECTURE.md", "# Architecture\n")
    _write(tmp_path / "tools/fleet-tui/package.json", "{}\n")
    _write(tmp_path / "Makefile", "check:\n\t@true\n")

    errors = AgentsMdValidator(tmp_path).validate_all()

    assert errors == []


def test_unexpected_nested_agent_guide_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "# Root\n\n- `tools/fleet-tui/AGENTS.md`\n")
    _write(
        tmp_path / "tools/fleet-tui/AGENTS.md",
        "# TUI\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )
    _write(
        tmp_path / "src/package/AGENTS.md",
        "# Client\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )
    _write(tmp_path / "CLAUDE.md", "@AGENTS.md\n")
    _write(tmp_path / AgentsMdValidator.DEVELOPMENT_SKILL, "# Skill\n")
    _write(tmp_path / "ARCHITECTURE.md", "# Architecture\n")
    _write(tmp_path / "Makefile", "check:\n\t@true\n")

    errors = AgentsMdValidator(tmp_path).validate_all()

    assert any(error.file == "src/package/AGENTS.md" and error.issue == "unexpected_nested_guide" for error in errors)


@pytest.mark.parametrize("example", ["`make check-docs`", "```bash\nmake check-docs\n```"])
@pytest.mark.parametrize("exists", [True, False])
def test_documented_make_targets(tmp_path: Path, example: str, exists: bool) -> None:
    _write(tmp_path / "AGENTS.md", example)
    _write(tmp_path / "Makefile", "check-docs:\n" if exists else "check:\n")
    validator = AgentsMdValidator(tmp_path)

    validator._validate_file(tmp_path / "AGENTS.md")

    assert [error.issue for error in validator.errors] == ([] if exists else ["invalid_makefile_target"])
    if not exists:
        assert "make check-docs" in validator.errors[0].detail


def test_make_prose_is_not_a_command(tmp_path: Path) -> None:
    _write(tmp_path / "Makefile", "check:\n")
    validator = AgentsMdValidator(tmp_path)
    validator._check_makefile_targets(tmp_path / "AGENTS.md", "We make changes locally.")
    assert validator.errors == []


@pytest.mark.parametrize("content", [None, "@OTHER.md\n", "@AGENTS.md\nExtra policy\n"])
def test_claude_import_must_be_canonical(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        _write(tmp_path / "CLAUDE.md", content)
    validator = AgentsMdValidator(tmp_path)
    validator._validate_claude_import()
    assert [error.issue for error in validator.errors] == ["invalid_import"]


def test_broken_guide_link(tmp_path: Path) -> None:
    validator = AgentsMdValidator(tmp_path)
    validator._check_links(tmp_path / "AGENTS.md", "[Architecture](missing.md#ownership)")
    assert [error.issue for error in validator.errors] == ["broken_link"]


def test_skill_references_are_reachable_and_links_resolve(tmp_path: Path) -> None:
    entry = tmp_path / AgentsMdValidator.DEVELOPMENT_SKILL
    _write(entry, "[Loop](references/loop.md)\n[Root](../../../AGENTS.md)")
    _write(tmp_path / "AGENTS.md", "# Root")
    _write(entry.parent / "references/loop.md", "[Broker](broker.md#timing)")
    _write(entry.parent / "references/broker.md", "[Loop](loop.md)\n[API](https://example.com)")
    validator = AgentsMdValidator(tmp_path)
    validator._validate_development_skill()
    assert validator.errors == []


@pytest.mark.parametrize("fault", ["missing", "orphan", "broken_nested"])
def test_skill_reference_failures(tmp_path: Path, fault: str) -> None:
    entry = tmp_path / AgentsMdValidator.DEVELOPMENT_SKILL
    _write(entry, "[Loop](references/loop.md)")
    if fault != "missing":
        _write(entry.parent / "references/loop.md", "[Absent](absent.md)" if fault == "broken_nested" else "# Loop")
    if fault == "orphan":
        _write(entry.parent / "references/orphan.md", "# Orphan")
    validator = AgentsMdValidator(tmp_path)
    validator._validate_development_skill()
    expected = "unreachable_reference" if fault == "orphan" else "broken_link"
    assert [error.issue for error in validator.errors] == [expected]


def test_missing_development_skill(tmp_path: Path) -> None:
    validator = AgentsMdValidator(tmp_path)
    validator._validate_development_skill()
    assert [error.issue for error in validator.errors] == ["missing_skill"]
