from pathlib import PurePosixPath

import pytest

from fleet_rlm.paths import UnsafePathError, VolumePaths, validate_project_slug
from fleet_rlm.workspace.paths import normalize_workspace_path


def _normalize(path: str, *, allow_root: bool = False) -> str:
    return normalize_workspace_path(path, allow_root=allow_root)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute.txt",
        "../escape.txt",
        "notes/../escape.txt",
        "notes\\escape.txt",
        ".fleet/config",
        "notes/.fleet/config",
        "./notes.txt",
    ],
)
def test_rejects_unsafe_file_paths(path: str) -> None:
    with pytest.raises(ValueError):
        _normalize(path)


def test_enforces_segment_and_total_utf8_bounds_without_an_arbitrary_depth_cap() -> None:
    deep = "/".join(["a"] * 9)
    assert _normalize(deep) == deep

    assert _normalize("a" * 255) == "a" * 255
    with pytest.raises(ValueError):
        _normalize("a" * 256)

    bounded = "/".join(["a" * 127] * 8)
    assert len(bounded.encode("utf-8")) == 1023
    assert _normalize(bounded) == bounded
    assert _normalize(bounded + "a") == bounded + "a"
    with pytest.raises(ValueError):
        _normalize(bounded + "aa")


def test_bounds_use_utf8_bytes_not_code_points() -> None:
    assert _normalize("é" * 127) == "é" * 127
    with pytest.raises(ValueError):
        _normalize("é" * 128)


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
