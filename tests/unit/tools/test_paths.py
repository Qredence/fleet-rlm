from __future__ import annotations

import pytest

from fleet_rlm.tools.paths import FilesystemSafetyError, safe_join_daytona_path, validate_relative_posix_path


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "%2e%2e/secret.txt",
        "safe%2f..%2fsecret.txt",
        "dir\\..\\secret.txt",
    ],
)
def test_validate_relative_posix_path_rejects_traversal(path: str) -> None:
    with pytest.raises(Exception, match="traversal|approved root|Backslash"):
        validate_relative_posix_path(path)


def test_validate_relative_posix_path_rejects_absolute_paths() -> None:
    with pytest.raises(Exception, match="approved root"):
        validate_relative_posix_path("/abs.txt")


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "/etc/passwd",
        "C:\\Users\\zocho\\.env",
    ],
)
def test_safe_join_daytona_path_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(FilesystemSafetyError):
        safe_join_daytona_path(path, base="/workspace/repo")
