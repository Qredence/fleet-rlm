"""Mount the local fleet-rlm source tree into a Daytona sandbox.

In local-dev mode (``repo_url`` is null), the sandbox otherwise has no source
tree — only a lossy markdown "workspace snapshot" staged at
``.fleet-rlm/context/``. So when a user asks the agent to analyze the codebase,
the agent's ``os.listdir("src/fleet_rlm/")`` gets ``FileNotFoundError`` and it
spends several dead-end iterations before pivoting to a doomed ``llm_query``
on the snapshot (observed in traces tr-0dc96586 and tr-5671ce47).

This module tars the local repo's source tree (``src/``, ``tests/``,
``scripts/``, top-level config) on the host, uploads the tarball to the
sandbox, and extracts it at the workspace root — which is the code
interpreter's cwd — so the agent's relative-path filesystem operations succeed.

Cloud-safe: detection requires the host cwd to look like a project root
(``pyproject.toml`` or ``.git``), which is not the case in serverless/cloud
deployments. An explicit opt-out is honoured via
``FLEET_RLM_MOUNT_LOCAL_REPO=false``.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import tarfile
from pathlib import Path
from typing import Any

from .protocols import DaytonaSandbox

logger = logging.getLogger(__name__)

# Hard caps to bound upload time and sandbox disk.
_MAX_COMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB per file

# Directories never useful in the sandbox and often huge.
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".venv",
        "__pycache__",
        "node_modules",
        ".git",
        ".codex",
        ".fleet-rlm",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
        "dist",
        "build",
        "site",
        ".next",
        ".turbo",
    }
)

# Top-level paths (relative to repo root) to include in the tarball.
# Directories are walked recursively (with exclusions); files are added as-is.
_DEFAULT_PATHS = ("src", "tests", "scripts", "pyproject.toml", "README.md", "README.rst")


def _looks_like_project_root(path: Path) -> bool:
    """Return True if ``path`` looks like a project root (pyproject.toml/.git)."""
    return any((path / marker).exists() for marker in ("pyproject.toml", ".git"))


def _mount_disabled_by_env() -> bool:
    raw = os.environ.get("FLEET_RLM_MOUNT_LOCAL_REPO", "").strip().lower()
    return raw in {"false", "0", "no", "off"}


def _resolve_local_repo_root() -> Path | None:
    """Locate the local repo root to mount, or ``None`` if not applicable.

    Zero-config: uses the host process cwd. Returns ``None`` when the cwd is
    not a project root (e.g. cloud/serverless) or when explicitly disabled.
    """
    if _mount_disabled_by_env():
        return None
    root = Path.cwd()
    if not _looks_like_project_root(root):
        return None
    return root


def _is_excluded(path: Path) -> bool:
    return any(part in _EXCLUDED_DIR_NAMES for part in path.parts)


def _build_repo_tarball(root: Path, *, paths: tuple[str, ...]) -> bytes | None:
    """Build a gzipped tarball of ``paths`` (relative to ``root``).

    Files larger than ``_MAX_FILE_BYTES`` are skipped. Returns the tarball
    bytes, or ``None`` if nothing could be added (e.g. none of ``paths`` exist).
    """
    buf = io.BytesIO()
    added = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in paths:
            absolute = root / rel
            if not absolute.exists():
                continue
            if absolute.is_file():
                if absolute.stat().st_size > _MAX_FILE_BYTES:
                    logger.debug("local_repo_mount: skipping large file %s", rel)
                    continue
                try:
                    tar.add(str(absolute), arcname=rel)
                    added += 1
                except Exception as e:
                    logger.debug("local_repo_mount: skipping unreadable/locked file %s: %s", rel, e)
            elif absolute.is_dir():
                for dirpath, dirnames, filenames in os.walk(absolute):
                    # Prune excluded dirs in-place so os.walk doesn't descend.
                    dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIR_NAMES]
                    for fname in filenames:
                        fpath = Path(dirpath) / fname
                        relpath = fpath.relative_to(root).as_posix()
                        try:
                            if fpath.stat().st_size > _MAX_FILE_BYTES:
                                continue
                        except OSError:
                            continue
                        try:
                            tar.add(str(fpath), arcname=relpath)
                            added += 1
                        except Exception as e:
                            logger.debug("local_repo_mount: skipping unreadable/locked file %s: %s", relpath, e)
    if added == 0:
        return None
    return buf.getvalue()


def _build_capped_repo_tarball(root: Path) -> bytes | None:
    """Build a tarball, degrading gracefully if it exceeds the size cap."""
    data = _build_repo_tarball(root, paths=_DEFAULT_PATHS)
    if data is not None and len(data) <= _MAX_COMPRESSED_BYTES:
        return data
    # Overrun (or empty): fall back to src/ only.
    logger.warning(
        "local_repo_mount: full tree tarball %s; retrying with src/ only",
        f"{len(data) if data else 0} bytes (> {_MAX_COMPRESSED_BYTES})" if data else "empty",
    )
    data = _build_repo_tarball(root, paths=("src",))
    if data is None or len(data) > _MAX_COMPRESSED_BYTES:
        logger.warning("local_repo_mount: src/-only tarball still too large or empty; skipping mount")
        return None
    return data


def _amount_local_repo_tree(*, sandbox: DaytonaSandbox, workspace_path: str) -> bool:
    """Mount the local repo source tree into the sandbox workspace.

    Returns ``True`` if the tree was mounted, ``False`` otherwise (including
    when no local repo is detectable or any step fails). Never raises — a
    failure falls back to the snapshot-only behaviour and does not break
    session creation.
    """
    root = _resolve_local_repo_root()
    if root is None:
        return False
    try:
        data = _build_capped_repo_tarball(root)
        if data is None:
            return False
        remote_tar = f"{workspace_path}/_repo.tar.gz"
        sandbox.fs.upload_file(data, remote_tar)
        # Extract at the workspace root (the code interpreter's cwd) so
        # os.listdir("src/fleet_rlm/") etc. resolve correctly. A non-zero
        # exit code doesn't raise from the SDK, so check it explicitly.
        extract_result = sandbox.process.exec(f"tar xzf {remote_tar} -C {workspace_path}")
        if int(getattr(extract_result, "exit_code", 0) or 0) != 0:
            logger.warning(
                "local_repo_mount: tar extraction failed (exit %s): %s",
                getattr(extract_result, "exit_code", "?"),
                getattr(extract_result, "result", getattr(extract_result, "std_out", "")),
            )
            return False
        # Clean up the tarball — it is transient.
        with contextlib.suppress(Exception):
            sandbox.process.exec(f"rm -f {remote_tar}")
        logger.info(
            "local_repo_mount: mounted %s into %s (%d bytes)",
            root,
            workspace_path,
            len(data),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - never break session creation
        logger.warning("local_repo_mount: failed to mount local repo tree (%s): %s", type(exc).__name__, exc)
        return False


def amount_local_repo_tree(*, sandbox: Any, workspace_path: str) -> bool:
    """Public sync entry point — see :func:`_amount_local_repo_tree`."""
    return _amount_local_repo_tree(sandbox=sandbox, workspace_path=workspace_path)


__all__ = ["amount_local_repo_tree", "_resolve_local_repo_root"]
