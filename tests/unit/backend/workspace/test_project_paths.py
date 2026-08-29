"""Project slug policy and projects/ volume layout (no live Daytona)."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from fleet_rlm.workspace.paths import (
    UnsafePathError,
    VolumePaths,
    validate_project_slug,
)


def test_projects_root_is_a_volume_sibling() -> None:
    paths = VolumePaths.from_mount()

    assert paths.projects_root() == PurePosixPath("/home/daytona/fleet/projects")
    assert paths.project_dir("fleet-rlm") == PurePosixPath("/home/daytona/fleet/projects/fleet-rlm")


def test_project_dir_uses_a_custom_mount() -> None:
    paths = VolumePaths.from_mount("/srv/fleet")

    assert paths.project_dir("ops.review") == PurePosixPath("/srv/fleet/projects/ops.review")


@pytest.mark.parametrize(
    "slug",
    [
        "a",
        "0",
        "fleet-rlm",
        "qredence.report_v2",
        "a..b",
        "b" * 64,
    ],
)
def test_accepts_canonical_slugs(slug: str) -> None:
    assert validate_project_slug(slug) == slug
    assert VolumePaths.from_mount().project_dir(slug).name == slug


@pytest.mark.parametrize(
    "slug",
    [
        "",  # empty
        "   ",  # whitespace only
        " fleet-rlm",  # leading whitespace
        "fleet-rlm ",  # trailing whitespace
        "Fleet",  # uppercase
        "_hidden",  # must start with [a-z0-9]
        ".hidden",
        "-hidden",
        "b" * 65,  # too long
        "équipe",  # unicode
        "project one",  # inner whitespace
        "a/b",  # separator
        "a\\b",  # backslash
        "..",  # traversal
        "../escape",
        "sessions",  # reserved volume roots
        "files",
        "artifacts",
        "attachments",
        "memory",
    ],
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


def test_reserved_slugs_match_system_roots_exactly() -> None:
    paths = VolumePaths.from_mount()
    for sibling in (
        paths.sessions_root(),
        paths.files_root(),
        paths.artifacts_root(),
        paths.attachments_root(),
        paths.projects_root(),
    ):
        assert sibling.parent == paths.mount_path
    # The reserved names are exactly the browsable system roots a slug could shadow.
    for reserved in ("sessions", "files", "artifacts", "attachments", "memory"):
        with pytest.raises(UnsafePathError, match="reserved"):
            paths.project_dir(reserved)
