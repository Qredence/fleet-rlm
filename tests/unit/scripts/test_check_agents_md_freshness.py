from __future__ import annotations

from pathlib import Path

from scripts.check_agents_md_freshness import AgentsMdValidator


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
