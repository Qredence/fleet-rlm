"""Project slug policy and projects/ volume layout (no live Daytona)."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from fleet_rlm.paths import UnsafePathError, VolumePaths, validate_project_slug


def test_projects_root_is_a_volume_sibling() -> None:
    paths = VolumePaths.from_mount()

    assert paths.projects_root() == PurePosixPath("/home/daytona/fleet/projects")
    assert paths.project_dir("fleet-rlm") == PurePosixPath("/home/daytona/fleet/projects/fleet-rlm")


@pytest.mark.parametrize(
    "slug",
    ["", "Fleet", "a/b", "a\\b", "sessions"],
)
def test_rejects_invalid_slugs(slug: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_project_slug(slug)
    with pytest.raises(UnsafePathError):
        VolumePaths.from_mount().project_dir(slug)


def test_rejects_nul_and_non_string_slugs() -> None:
    with pytest.raises(UnsafePathError):
        validate_project_slug("fleet\x00rlm")
    for value in (None, 7, b"fleet-rlm"):
        with pytest.raises(UnsafePathError):
            validate_project_slug(value)  # type: ignore[arg-type]
