"""Tests for scaffold module installation and listing behavior."""

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
    assert (scaffold_dir / "teams").exists()
    assert (scaffold_dir / "hooks").exists()


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


def test_list_agents_returns_all_9():
    """Should list top-level and nested team agents."""
    agents = scaffold.list_agents()
    assert len(agents) == 9
    # Check that each agent has the expected fields
    for agent in agents:
        assert "name" in agent
        assert "description" in agent
        assert "model" in agent
        assert "path" in agent
        assert isinstance(agent["name"], str)
        assert isinstance(agent["description"], str)
        assert isinstance(agent["model"], str)


def test_list_agents_includes_expected_names():
    """Should include both root and teams/ agent definitions."""
    agents = scaffold.list_agents()
    agent_names = {a["name"] for a in agents}
    expected = {
        "modal-interpreter-agent",
        "rlm-orchestrator",
        "rlm-specialist",
        "rlm-subcall",
        "teams/agent-designer",
        "teams/architect-explorer",
        "teams/fleet-rlm-explorer-team",
        "teams/testing-analyst",
        "teams/ux-reviewer",
    }
    assert agent_names == expected


def test_list_teams_returns_expected_template():
    """Should list fleet-rlm team template metadata."""
    teams = scaffold.list_teams()
    assert len(teams) == 1
    assert teams[0]["name"] == "fleet-rlm"
    assert teams[0]["files"] > 0
    assert "description" in teams[0]


def test_list_hooks_returns_expected_files():
    """Should list hook templates."""
    hooks = scaffold.list_hooks()
    hook_names = {h["name"] for h in hooks}
    expected = {
        "README.md",
        "hookify.fleet-rlm-document-process.local.md",
        "hookify.fleet-rlm-large-file.local.md",
        "hookify.fleet-rlm-llm-query-error.local.md",
        "hookify.fleet-rlm-modal-error.local.md",
    }
    assert hook_names == expected


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

    # Should install top-level + nested team agents
    assert len(installed) == 9

    # Check that the agents directory was created
    agents_dir = target / "agents"
    assert agents_dir.exists()

    # Check that each agent file exists (including nested paths)
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
    assert len(installed_first) == 9

    # Second install without force should skip all
    installed_second = scaffold.install_agents(target, force=False)
    assert len(installed_second) == 0


def test_install_agents_force_overwrites(tmp_path: Path):
    """Should overwrite existing agents when force=True."""
    target = tmp_path / "test_claude"

    # First install
    installed_first = scaffold.install_agents(target, force=False)
    assert len(installed_first) == 9

    # Modify a file to verify overwrite
    orchestrator = target / "agents" / "rlm-orchestrator.md"
    original_content = orchestrator.read_text()
    orchestrator.write_text("MODIFIED")

    # Second install with force should overwrite all
    installed_second = scaffold.install_agents(target, force=True)
    assert len(installed_second) == 9

    # Verify the file was restored
    assert orchestrator.read_text() == original_content


def test_install_teams_creates_files(tmp_path: Path):
    """Should install team templates under target/teams."""
    target = tmp_path / "test_claude"
    installed = scaffold.install_teams(target, force=False)
    assert installed == ["fleet-rlm"]
    assert (target / "teams" / "fleet-rlm" / "config.json").exists()
    assert (
        target / "teams" / "fleet-rlm" / "inboxes" / "rlm-orchestrator.json"
    ).exists()


def test_install_hooks_creates_files(tmp_path: Path):
    """Should install hook templates under target/hooks."""
    target = tmp_path / "test_claude"
    installed = scaffold.install_hooks(target, force=False)
    assert "hookify.fleet-rlm-document-process.local.md" in installed
    assert (target / "hooks" / "README.md").exists()


def test_install_teams_skips_existing(tmp_path: Path):
    """Should skip teams when force=False and already present."""
    target = tmp_path / "test_claude"
    first = scaffold.install_teams(target, force=False)
    second = scaffold.install_teams(target, force=False)
    assert first == ["fleet-rlm"]
    assert second == []


def test_install_hooks_skips_existing(tmp_path: Path):
    """Should skip hooks when force=False and already present."""
    target = tmp_path / "test_claude"
    first = scaffold.install_hooks(target, force=False)
    second = scaffold.install_hooks(target, force=False)
    assert len(first) == len(scaffold.list_hooks())
    assert second == []


def test_install_all_returns_summary(tmp_path: Path):
    """Should install all scaffold categories and return summary."""
    target = tmp_path / "test_claude"
    result = scaffold.install_all(target, force=False)

    # Check structure
    assert "skills_installed" in result
    assert "agents_installed" in result
    assert "teams_installed" in result
    assert "hooks_installed" in result
    assert "skills_total" in result
    assert "agents_total" in result
    assert "teams_total" in result
    assert "hooks_total" in result

    # Check counts
    assert len(result["skills_installed"]) == 10
    assert len(result["agents_installed"]) == 9
    assert len(result["teams_installed"]) == 1
    assert len(result["hooks_installed"]) == len(scaffold.list_hooks())
    assert result["skills_total"] == 10
    assert result["agents_total"] == 9
    assert result["teams_total"] == 1
    assert result["hooks_total"] == len(scaffold.list_hooks())

    # Verify files exist
    assert (target / "skills" / "rlm" / "SKILL.md").exists()
    assert (target / "agents" / "rlm-orchestrator.md").exists()
    assert (target / "agents" / "teams" / "agent-designer.md").exists()
    assert (target / "teams" / "fleet-rlm" / "config.json").exists()
    assert (target / "hooks" / "hookify.fleet-rlm-modal-error.local.md").exists()


def test_install_all_skips_partial_existing(tmp_path: Path):
    """Should skip existing and only install missing categories."""
    target = tmp_path / "test_claude"

    # Install skills only first
    scaffold.install_skills(target, force=False)

    # Then install all (should skip skills, install others)
    result = scaffold.install_all(target, force=False)

    assert len(result["skills_installed"]) == 0  # All skipped
    assert len(result["agents_installed"]) == 9  # Newly installed
    assert len(result["teams_installed"]) == 1
    assert len(result["hooks_installed"]) == len(scaffold.list_hooks())
    assert result["skills_total"] == 10
    assert result["agents_total"] == 9
    assert result["teams_total"] == 1
    assert result["hooks_total"] == len(scaffold.list_hooks())


def test_install_all_can_skip_teams_and_hooks(tmp_path: Path):
    """install_all should respect include_teams/include_hooks flags."""
    target = tmp_path / "test_claude"
    result = scaffold.install_all(
        target, force=False, include_teams=False, include_hooks=False
    )
    assert result["teams_installed"] == []
    assert result["hooks_installed"] == []
    assert not (target / "teams").exists()
    assert not (target / "hooks").exists()
