from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.skills.active import ActiveSkills
from fleet_rlm.skills.execution_deps import SkillExecutionDeps


def test_skill_execution_deps_reads_active_skills_when_present() -> None:
    """Optional path: callers may attach _active_skills; not required for correctness."""
    active = ActiveSkills(
        selected=["alpha"],
        resources={"alpha": []},
        sandbox_paths={"alpha": "/home/daytona/memory/skills/system/alpha"},
    )
    runtime = SimpleNamespace(
        _selected_skill_ids=["alpha"],
        _active_skills=active,
    )

    deps = SkillExecutionDeps.from_runtime(runtime, volume_mount_path="/home/daytona/memory")

    assert deps.selected_skill_ids == ("alpha",)
    assert deps.resources == active.resources
    assert deps.sandbox_paths == active.sandbox_paths


def test_skill_execution_deps_without_active_skills_uses_selected_ids_only() -> None:
    runtime = SimpleNamespace(_selected_skill_ids=["alpha"])

    deps = SkillExecutionDeps.from_runtime(runtime, volume_mount_path="/home/daytona/memory")

    assert deps.selected_skill_ids == ("alpha",)
    assert deps.resources is None
    assert deps.sandbox_paths is None
