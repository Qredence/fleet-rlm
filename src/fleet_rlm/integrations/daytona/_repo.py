"""Git ref resolution, path helpers, repo clone, checkout reconciliation,
and local repo tree mounting for Daytona sandboxes.

Merged from ``_git_helpers.py`` and ``_local_repo_mount.py``.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import shlex
import subprocess
import tarfile
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from .diagnostics import DaytonaDiagnosticError
from .protocols import DaytonaSandbox
from .volumes import ensure_remote_directory as _aensure_remote_directory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =========================================================================
# Git helpers (merged from _git_helpers.py)
# =========================================================================


# ---------------------------------------------------------------------------
# Workspace path helpers
# ---------------------------------------------------------------------------


def _safe_repo_name(repo_url: str) -> str:
    tail = repo_url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.removesuffix(".git")
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    return cleaned or "repo"


def _safe_workspace_name(repo_url: str | None) -> str:
    return _safe_repo_name(repo_url) if repo_url else "daytona-workspace"


def _aget_work_dir(sandbox: Any) -> str:
    if hasattr(sandbox, "get_work_dir"):
        return str(sandbox.get_work_dir())
    return "/workspace"


def _abuild_workspace_path(sandbox: DaytonaSandbox, repo_url: str | None) -> str:
    work_dir = _aget_work_dir(sandbox)
    workspace_name = _safe_workspace_name(repo_url)
    return str(PurePosixPath(work_dir) / "workspace" / workspace_name)


def _aensure_workspace_root(*, sandbox: DaytonaSandbox, workspace_path: str) -> None:
    try:
        _aensure_remote_directory(sandbox.fs, PurePosixPath(workspace_path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona workspace create failure: {exc}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc


# ---------------------------------------------------------------------------
# Git ref resolution helpers
# ---------------------------------------------------------------------------

_REMOTE_REF_RESOLUTION_TIMEOUT_S = 5


def _looks_like_commit(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref.strip()))


def _list_remote_refs(repo_url: str) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--heads", "--tags", repo_url],
            capture_output=True,
            check=False,
            text=True,
            timeout=_REMOTE_REF_RESOLUTION_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    if completed.returncode != 0:
        return set()

    refs: set[str] = set()
    for line in completed.stdout.splitlines():
        if "\t" not in line:
            continue
        _sha, raw_ref = line.split("\t", 1)
        normalized = raw_ref.strip()
        if normalized.startswith("refs/heads/"):
            refs.add(normalized.removeprefix("refs/heads/"))
            continue
        if normalized.startswith("refs/tags/"):
            refs.add(normalized.removeprefix("refs/tags/").removesuffix("^{}"))
    return refs


def _resolve_clone_ref(repo_url: str, ref: str | None) -> str | None:
    normalized = str(ref or "").strip() or None
    if normalized is None or _looks_like_commit(normalized) or "/" not in normalized:
        return normalized

    remote_refs = _list_remote_refs(repo_url)
    if not remote_refs or normalized in remote_refs:
        return normalized

    segments = [segment for segment in normalized.split("/") if segment]
    for end in range(len(segments) - 1, 0, -1):
        candidate = "/".join(segments[:end])
        if candidate in remote_refs:
            return candidate
    return normalized


def _aresolve_clone_ref(repo_url: str, ref: str | None) -> str | None:
    return _resolve_clone_ref(repo_url, ref)


# ---------------------------------------------------------------------------
# Repo clone and checkout reconciliation
# ---------------------------------------------------------------------------


def _build_clone_kwargs(
    *,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> dict[str, str]:
    clone_kwargs: dict[str, str] = {"url": repo_url, "path": workspace_path}
    if ref:
        if _looks_like_commit(ref):
            clone_kwargs["commit_id"] = ref
        else:
            clone_kwargs["branch"] = ref
    return clone_kwargs


def _aclone_repo(
    *,
    sandbox: DaytonaSandbox,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
    shallow: bool = False,
) -> None:
    try:
        _aensure_remote_directory(
            sandbox.fs,
            PurePosixPath(workspace_path).parent,
        )
        clone_kwargs = _build_clone_kwargs(
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        )
        if shallow:
            try:
                sandbox.git.clone(**clone_kwargs, depth=1)
            except TypeError:
                # SDK doesn't support depth kwarg; fall back to exec
                branch_args = f" --branch {clone_kwargs['branch']}" if "branch" in clone_kwargs else ""
                cmd = f"git clone --depth=1{branch_args} {repo_url} {workspace_path}"
                sandbox.process.exec(cmd)
        else:
            sandbox.git.clone(**clone_kwargs)
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona repo clone failure: {exc}",
            category="sandbox_create_clone_error",
            phase="repo_clone",
        ) from exc


def _areconcile_repo_checkout(
    *,
    sandbox: DaytonaSandbox,
    repo_url: str | None,
    ref: str | None,
    workspace_path: str,
) -> None:
    if repo_url is None:
        _aensure_workspace_root(
            sandbox=sandbox,
            workspace_path=workspace_path,
        )
        return

    if not _apath_exists(sandbox=sandbox, path=workspace_path):
        _aclone_repo(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        )
        return

    if not _apath_has_git_metadata(sandbox=sandbox, path=workspace_path):
        _areplace_repo_checkout(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        )
        return

    remote_url = _agit_remote_url(sandbox=sandbox, workspace_path=workspace_path)
    if remote_url != repo_url:
        _areplace_repo_checkout(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        )
        return

    if ref is None:
        _apull_repo_checkout(sandbox=sandbox, workspace_path=workspace_path)
        return

    if _looks_like_commit(ref):
        _aforce_checkout_ref(
            sandbox=sandbox,
            workspace_path=workspace_path,
            ref=ref,
            detached=True,
        )
        return

    if _acheckout_branch_with_sdk(
        sandbox=sandbox,
        workspace_path=workspace_path,
        ref=ref,
    ):
        logger.debug("Checked out Daytona repo branch via SDK before force reconcile")

    _aforce_checkout_ref(
        sandbox=sandbox,
        workspace_path=workspace_path,
        ref=ref,
        detached=False,
    )


def _areplace_repo_checkout(
    *,
    sandbox: DaytonaSandbox,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> None:
    """Replace a mismatched or non-git checkout before SDK cloning."""
    _aexec_sandbox_command(
        sandbox=sandbox,
        command=shlex.join(["rm", "-rf", "--", workspace_path]),
        phase="repo_clone",
        error_prefix="Daytona repo replace failure",
    )
    _aclone_repo(
        sandbox=sandbox,
        repo_url=repo_url,
        ref=ref,
        workspace_path=workspace_path,
    )


def _apath_exists(*, sandbox: DaytonaSandbox, path: str) -> bool:
    result = _aexec_sandbox_command(
        sandbox=sandbox,
        command=f"test -e {shlex.quote(path)}",
        phase="repo_clone",
        error_prefix="Daytona repo path probe failure",
        check=False,
    )
    return _sandbox_exec_exit_code(result) == 0


def _apath_has_git_metadata(*, sandbox: DaytonaSandbox, path: str) -> bool:
    result = _aexec_sandbox_command(
        sandbox=sandbox,
        command=f"test -d {shlex.quote(str(PurePosixPath(path) / '.git'))}",
        phase="repo_clone",
        error_prefix="Daytona repo git probe failure",
        check=False,
    )
    return _sandbox_exec_exit_code(result) == 0


def _agit_remote_url(*, sandbox: DaytonaSandbox, workspace_path: str) -> str | None:
    result = _aexec_git_command(
        sandbox=sandbox,
        workspace_path=workspace_path,
        args=("remote", "get-url", "origin"),
        check=False,
    )
    if _sandbox_exec_exit_code(result) != 0:
        return None
    return _sandbox_exec_stdout(result).strip() or None


def _apull_repo_checkout(*, sandbox: DaytonaSandbox, workspace_path: str) -> None:
    try:
        with suppress(Exception):
            sandbox.git.status(workspace_path)
        sandbox.git.pull(workspace_path)
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona repo pull failure: {exc}",
            category="sandbox_create_clone_error",
            phase="repo_clone",
        ) from exc


def _acheckout_branch_with_sdk(
    *,
    sandbox: DaytonaSandbox,
    workspace_path: str,
    ref: str,
) -> bool:
    try:
        branches = sandbox.git.branches(workspace_path)
        branch_names = _extract_sdk_branch_names(branches)
        if branch_names and ref not in branch_names:
            return False
        sandbox.git.checkout_branch(workspace_path, ref)
        return True
    except Exception:
        return False


def _extract_sdk_branch_names(branches: Any) -> set[str]:
    raw_branches = getattr(branches, "branches", branches)
    raw_items = raw_branches.keys() if isinstance(raw_branches, dict) else raw_branches
    names: set[str] = set()
    for item in raw_items or []:
        name = getattr(item, "name", item)
        if name is not None:
            names.add(str(name).removeprefix("origin/"))
    return names


def _aforce_checkout_ref(
    *,
    sandbox: DaytonaSandbox,
    workspace_path: str,
    ref: str,
    detached: bool,
) -> None:
    _aexec_git_command(
        sandbox=sandbox,
        workspace_path=workspace_path,
        args=("fetch", "--all", "--tags", "--prune"),
    )
    if detached:
        _aexec_git_command(
            sandbox=sandbox,
            workspace_path=workspace_path,
            args=("checkout", "--force", ref),
        )
        return

    remote_ref = f"refs/remotes/origin/{ref}"
    remote_probe = _agit_ref_probe(
        sandbox=sandbox,
        workspace_path=workspace_path,
        ref=remote_ref,
    )
    local_probe = _agit_ref_probe(
        sandbox=sandbox,
        workspace_path=workspace_path,
        ref=ref,
    )
    if remote_probe:
        branch_exists = _agit_ref_probe(
            sandbox=sandbox,
            workspace_path=workspace_path,
            ref=f"refs/heads/{ref}",
            verify_arg="show-ref",
        )
        if branch_exists:
            _aexec_git_command(
                sandbox=sandbox,
                workspace_path=workspace_path,
                args=("checkout", "--force", ref),
            )
        else:
            _aexec_git_command(
                sandbox=sandbox,
                workspace_path=workspace_path,
                args=("checkout", "--force", "-B", ref, remote_ref),
            )
        _aexec_git_command(
            sandbox=sandbox,
            workspace_path=workspace_path,
            args=("reset", "--hard", remote_ref),
        )
        return

    if local_probe:
        _aexec_git_command(
            sandbox=sandbox,
            workspace_path=workspace_path,
            args=("checkout", "--force", ref),
        )
        return

    raise DaytonaDiagnosticError(
        f"Daytona repo checkout failure: requested ref is not available: {ref}",
        category="sandbox_create_clone_error",
        phase="repo_clone",
    )


def _agit_ref_probe(
    *,
    sandbox: DaytonaSandbox,
    workspace_path: str,
    ref: str,
    verify_arg: str = "rev-parse",
) -> bool:
    args = ("show-ref", "--verify", ref) if verify_arg == "show-ref" else ("rev-parse", "--verify", ref)
    result = _aexec_git_command(
        sandbox=sandbox,
        workspace_path=workspace_path,
        args=args,
        check=False,
    )
    return _sandbox_exec_exit_code(result) == 0


def _aexec_git_command(
    *,
    sandbox: DaytonaSandbox,
    workspace_path: str,
    args: tuple[str, ...],
    check: bool = True,
) -> Any:
    return _aexec_sandbox_command(
        sandbox=sandbox,
        command=shlex.join(["git", "-C", workspace_path, *args]),
        phase="repo_clone",
        error_prefix="Daytona repo git failure",
        check=check,
    )


def _aexec_sandbox_command(
    *,
    sandbox: DaytonaSandbox,
    command: str,
    phase: str,
    error_prefix: str,
    category: str = "sandbox_create_clone_error",
    check: bool = True,
) -> Any:
    try:
        result = sandbox.process.exec(command)
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"{error_prefix}: {exc}",
            category=category,
            phase=phase,
        ) from exc

    if check and _sandbox_exec_exit_code(result):
        raise DaytonaDiagnosticError(
            f"{error_prefix}: {_sandbox_exec_output(result)}",
            category=category,
            phase=phase,
        )
    return result


def _sandbox_exec_exit_code(result: Any) -> int:
    return int(getattr(result, "exit_code", 0) or 0)


def _sandbox_exec_output(result: Any) -> str:
    return str(
        getattr(result, "stderr", "")
        or getattr(result, "result", "")
        or getattr(getattr(result, "artifacts", None), "stdout", "")
        or getattr(result, "stdout", "")
        or getattr(result, "output", "")
        or "sandbox command failed"
    )


def _sandbox_exec_stdout(result: Any) -> str:
    return str(
        getattr(result, "stdout", "")
        or getattr(result, "result", "")
        or getattr(getattr(result, "artifacts", None), "stdout", "")
        or getattr(result, "output", "")
        or ""
    )


# =========================================================================
# Local repo tree mounting (merged from _local_repo_mount.py)
# =========================================================================


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
    """Locate the local repo root to mount, or ``None`` if not applicable."""
    if _mount_disabled_by_env():
        return None
    root = Path.cwd()
    if not _looks_like_project_root(root):
        return None
    return root


def _is_excluded(path: Path) -> bool:
    return any(part in _EXCLUDED_DIR_NAMES for part in path.parts)


def _build_repo_tarball(root: Path, *, paths: tuple[str, ...]) -> bytes | None:
    """Build a gzipped tarball of ``paths`` (relative to ``root``)."""
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


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "amount_local_repo_tree",
    "_abuild_workspace_path",
    "_aclone_repo",
    "_aensure_workspace_root",
    "_areconcile_repo_checkout",
    "_aresolve_clone_ref",
    "_resolve_local_repo_root",
]
