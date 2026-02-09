"""Tests for the scaffold module (skills/agents installation)."""

from __future__ import annotations

from pathlib import Path

from fleet_rlm import scaffold


def test_get_scaffold_dir_exists():
    """The scaffold directory should exist in the installed package."""
    scaffold_dir = scaffold.get_scaffold_dir()
    assert scaffold_dir.exists()
    assert scaffold_dir.is_dir()
    assert (scaffold_dir / "skills").exists()
    assert (scaffold_dir / "agents").exists()


def test_list_skills_returns_all_10():
    """Should list all 10 available skills."""
    skills = scaffold.list_skills()
    assert len(skills) == 10
    # Check that each skill has the expected fields
    for skill in skills:
        assert "name" in skill
        assert "description" in skill
        assert "files" in skill
        assert isinstance(skill["name"], str)
        assert isinstance(skill["description"], str)
        assert isinstance(skill["files"], int)
        assert skill["files"] > 0


def test_list_skills_includes_expected_names():
    """Should include the known skill names."""
    skills = scaffold.list_skills()
    skill_names = {s["name"] for s in skills}
    expected = {
        "dspy-signature",
        "modal-sandbox",
        "rlm",
        "rlm-batch",
        "rlm-debug",
        "rlm-execute",
        "rlm-long-context",
        "rlm-memory",
        "rlm-run",
        "rlm-test-suite",
    }
    assert skill_names == expected


def test_list_agents_returns_all_4():
    """Should list all 4 available agents."""
    agents = scaffold.list_agents()
    assert len(agents) == 4
    # Check that each agent has the expected fields
    for agent in agents:
        assert "name" in agent
        assert "description" in agent
        assert "model" in agent
        assert isinstance(agent["name"], str)
        assert isinstance(agent["description"], str)
        assert isinstance(agent["model"], str)


def test_list_agents_includes_expected_names():
    """Should include the known agent names."""
    agents = scaffold.list_agents()
    agent_names = {a["name"] for a in agents}
    expected = {
        "modal-interpreter-agent",
        "rlm-orchestrator",
        "rlm-specialist",
        "rlm-subcall",
    }
    assert agent_names == expected


def test_install_skills_creates_files(tmp_path: Path):
    """Should create skill directories and files in target."""
    target = tmp_path / "test_claude"
    installed = scaffold.install_skills(target, force=False)

    # Should install all 10 skills
    assert len(installed) == 10

    # Check that the skills directory was created
    skills_dir = target / "skills"
    assert skills_dir.exists()

    # Check that each skill has a SKILL.md file
    for skill_name in installed:
        skill_dir = skills_dir / skill_name
        assert skill_dir.exists()
        assert (skill_dir / "SKILL.md").exists()


def test_install_skills_skips_existing(tmp_path: Path):
    """Should skip existing skills when force=False."""
    target = tmp_path / "test_claude"

    # First install
    installed_first = scaffold.install_skills(target, force=False)
    assert len(installed_first) == 10

    # Second install without force should skip all
    installed_second = scaffold.install_skills(target, force=False)
    assert len(installed_second) == 0


def test_install_skills_force_overwrites(tmp_path: Path):
    """Should overwrite existing skills when force=True."""
    target = tmp_path / "test_claude"

    # First install
    installed_first = scaffold.install_skills(target, force=False)
    assert len(installed_first) == 10

    # Modify a file to verify overwrite
    rlm_skill = target / "skills" / "rlm" / "SKILL.md"
    original_content = rlm_skill.read_text()
    rlm_skill.write_text("MODIFIED")
    assert rlm_skill.read_text() == "MODIFIED"

    # Second install with force should overwrite all
    installed_second = scaffold.install_skills(target, force=True)
    assert len(installed_second) == 10

    # Verify the file was restored
    assert rlm_skill.read_text() == original_content
    assert "MODIFIED" not in rlm_skill.read_text()


def test_install_agents_creates_files(tmp_path: Path):
    """Should create agent files in target."""
    target = tmp_path / "test_claude"
    installed = scaffold.install_agents(target, force=False)

    # Should install all 4 agents
    assert len(installed) == 4

    # Check that the agents directory was created
    agents_dir = target / "agents"
    assert agents_dir.exists()

    # Check that each agent file exists
    for agent_name in installed:
        agent_file = agents_dir / f"{agent_name}.md"
        assert agent_file.exists()
        # Verify it has YAML frontmatter
        content = agent_file.read_text()
        assert content.startswith("---\n")


def test_install_agents_skips_existing(tmp_path: Path):
    """Should skip existing agents when force=False."""
    target = tmp_path / "test_claude"

    # First install
    installed_first = scaffold.install_agents(target, force=False)
    assert len(installed_first) == 4

    # Second install without force should skip all
    installed_second = scaffold.install_agents(target, force=False)
    assert len(installed_second) == 0


def test_install_agents_force_overwrites(tmp_path: Path):
    """Should overwrite existing agents when force=True."""
    target = tmp_path / "test_claude"

    # First install
    installed_first = scaffold.install_agents(target, force=False)
    assert len(installed_first) == 4

    # Modify a file to verify overwrite
    orchestrator = target / "agents" / "rlm-orchestrator.md"
    original_content = orchestrator.read_text()
    orchestrator.write_text("MODIFIED")

    # Second install with force should overwrite all
    installed_second = scaffold.install_agents(target, force=True)
    assert len(installed_second) == 4

    # Verify the file was restored
    assert orchestrator.read_text() == original_content


def test_install_all_returns_summary(tmp_path: Path):
    """Should install both skills and agents and return summary."""
    target = tmp_path / "test_claude"
    result = scaffold.install_all(target, force=False)

    # Check structure
    assert "skills_installed" in result
    assert "agents_installed" in result
    assert "skills_total" in result
    assert "agents_total" in result

    # Check counts
    assert len(result["skills_installed"]) == 10
    assert len(result["agents_installed"]) == 4
    assert result["skills_total"] == 10
    assert result["agents_total"] == 4

    # Verify files exist
    assert (target / "skills" / "rlm" / "SKILL.md").exists()
    assert (target / "agents" / "rlm-orchestrator.md").exists()


def test_install_all_skips_partial_existing(tmp_path: Path):
    """Should skip existing and only install new when force=False."""
    target = tmp_path / "test_claude"

    # Install skills only first
    scaffold.install_skills(target, force=False)

    # Then install all (should skip skills, install agents)
    result = scaffold.install_all(target, force=False)

    assert len(result["skills_installed"]) == 0  # All skipped
    assert len(result["agents_installed"]) == 4  # Newly installed
    assert result["skills_total"] == 10
    assert result["agents_total"] == 4
