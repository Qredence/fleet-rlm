"""Seed bundled scaffold skills into Daytona volume layout."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from fleet_rlm.skills.catalog import iter_scaffold_skill_markdown

logger = logging.getLogger(__name__)


def iter_scaffold_skill_pairs() -> Iterator[tuple[str, str]]:
    yield from iter_scaffold_skill_markdown()


def seed_system_skills(mounted_root: str) -> None:
    """Seed bundled scaffold skills into ``{mounted_root}/skills/system/``.

    Idempotent — skips any skill file that already exists.
    """
    dest_dir = Path(mounted_root) / "skills" / "system"
    if not dest_dir.exists():
        logger.debug("seed_system_skills: skills/system not found, skipping seed")
        return

    try:
        for skill_name, instructions in iter_scaffold_skill_markdown():
            try:
                dest_file = dest_dir / f"{skill_name}.md"
                if dest_file.exists():
                    continue
                dest_file.write_text(instructions, encoding="utf-8")
                logger.debug("seed_system_skills: seeded %s", skill_name)
            except Exception as exc:
                logger.warning("seed_system_skills: skipped %s: %s", skill_name, exc)
    except Exception as exc:
        logger.warning("seed_system_skills: skill seeding failed (non-fatal): %s", exc)


__all__ = ["iter_scaffold_skill_pairs", "seed_system_skills"]
