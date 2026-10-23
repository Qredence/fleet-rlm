"""Git-aware local path discovery shared by mention search and directory uploads."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from functools import lru_cache
from itertools import islice
from pathlib import Path

_SEARCH_INDEX_TTL_SECONDS = 60


def _git_files(root: Path) -> list[Path] | None:
    """Return Git-listed relative paths after applying ignore rules, or None outside a Git tree.

    Raise ValueError if Git or its ignore check fails; an empty Git listing returns [].
    """
    if not any((parent / ".git").exists() for parent in (root, *root.parents)):
        return None
    try:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."],
            capture_output=True,
            check=True,
            timeout=3,
        ).stdout
        if not listed:
            return []
        # --no-index also excludes ignored files which happen to be tracked.
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--no-index", "--stdin", "-z"],
            input=listed,
            capture_output=True,
            check=False,
            timeout=3,
        )
        if ignored.returncode not in {0, 1}:
            raise ValueError("Cannot inspect Git ignore rules for directory mentions")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Git is required to inspect ignore rules for this directory") from exc
    excluded = set(ignored.stdout.split(b"\0"))
    return sorted({Path(os.fsdecode(path)) for path in listed.split(b"\0") if path and path not in excluded})


def _safe_relative_file(root: Path, relative: Path) -> bool:
    """Return whether a relative path names a regular file without hidden, parent, or symlink components."""
    if relative.is_absolute() or ".." in relative.parts or any(part.startswith(".") for part in relative.parts):
        return False
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            return False
    return path.is_file()


def iter_local_files(root: Path) -> Iterator[Path]:
    """Yield non-hidden regular files, honoring Git ignores and never following symlinks.

    Git working trees require a working Git executable; errors fail closed rather
    than silently uploading ignored data. Outside Git, hidden files and symlinks
    are still excluded. Results are paths relative to the selected directory.
    """
    files = _git_files(root)
    if files is not None:
        for relative in files:
            if _safe_relative_file(root, relative):
                yield relative
        return

    def failed(error: OSError) -> None:
        """Propagate directory-walk errors instead of returning a partial upload listing."""
        raise error

    for directory, folders, names in os.walk(root, followlinks=False, onerror=failed):
        parent = Path(directory)
        folders[:] = sorted(name for name in folders if not name.startswith(".") and not (parent / name).is_symlink())
        for name in sorted(names):
            relative = (parent / name).relative_to(root)
            if _safe_relative_file(root, relative):
                yield relative


@lru_cache(maxsize=4)
def _search_index(root: Path, _generation: int) -> tuple[Path, ...]:
    """Cache discoverable files and ancestor directories for a caller-supplied generation.

    The generation changes in one-minute windows during path search. Discovery examines at most
    20,000 eligible files and stops after an iteration exceeds 0.25 seconds; filesystem
    and Git-discovery errors propagate.
    """
    paths: set[Path] = set()
    started = time.monotonic()
    # Search is best-effort and bounded; explicit directory uploads are separately
    # validated and never silently truncated to this search window.
    for relative in islice(iter_local_files(root), 20_000):
        paths.add(relative)
        paths.update(parent for parent in relative.parents if parent != Path("."))
        if time.monotonic() - started > 0.25:
            break
    return tuple(paths)


def search_local_paths(fragment: str) -> list[Path]:
    """Find case-insensitive basename substring matches under the current working directory.

    Uses a bounded index refreshed on demand in one-minute windows. Sort exact matches first,
    then prefixes, shallower paths, and path text. Return [] on filesystem or Git-discovery errors.
    """
    try:
        paths = _search_index(Path.cwd(), int(time.monotonic() // _SEARCH_INDEX_TTL_SECONDS))
    except (OSError, ValueError):
        return []  # Immediate-directory completion remains available if indexing fails.
    needle = fragment.casefold()
    matches = [path for path in paths if needle in path.name.casefold()]
    return sorted(
        matches,
        key=lambda path: (
            path.name.casefold() != needle,
            not path.name.casefold().startswith(needle),
            len(path.parts),
            path.as_posix().casefold(),
        ),
    )
