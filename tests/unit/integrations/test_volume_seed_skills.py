from __future__ import annotations

from pathlib import Path

from fleet_rlm.integrations.daytona.volumes import seed_system_skills


def test_seeds_scaffold_skills(tmp_path: Path) -> None:
    system_dir = tmp_path / "skills" / "system"
    system_dir.mkdir(parents=True)
    seed_system_skills(str(tmp_path))
    md_files = list(system_dir.glob("*.md"))
    assert len(md_files) >= 1


def test_idempotent_seed(tmp_path: Path) -> None:
    system_dir = tmp_path / "skills" / "system"
    system_dir.mkdir(parents=True)
    seed_system_skills(str(tmp_path))
    count_first = len(list(system_dir.glob("*.md")))
    seed_system_skills(str(tmp_path))
    count_second = len(list(system_dir.glob("*.md")))
    assert count_first == count_second


def test_skips_when_system_dir_missing(tmp_path: Path) -> None:
    seed_system_skills(str(tmp_path))
    assert not (tmp_path / "skills" / "system").exists()
