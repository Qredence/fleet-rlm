"""Git repository clone and reconciliation helpers for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import PurePosixPath
from typing import Any

from .admin import _arun_admin_code
from .async_compat import _await_if_needed
from .diagnostics import DaytonaDiagnosticError
from .workspace import _aensure_remote_directory, _aensure_workspace_root

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


async def _aresolve_clone_ref(repo_url: str, ref: str | None) -> str | None:
    return await asyncio.to_thread(_resolve_clone_ref, repo_url, ref)


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


async def _aclone_repo(
    *,
    sandbox: Any,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> None:
    try:
        await _aensure_remote_directory(
            sandbox.fs, PurePosixPath(workspace_path).parent
        )
        await _await_if_needed(
            sandbox.git.clone(
                **_build_clone_kwargs(
                    repo_url=repo_url,
                    ref=ref,
                    workspace_path=workspace_path,
                )
            )
        )
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona repo clone failure: {exc}",
            category="sandbox_create_clone_error",
            phase="repo_clone",
        ) from exc


async def _areconcile_repo_checkout(
    *,
    sandbox: Any,
    repo_url: str | None,
    ref: str | None,
    workspace_path: str,
) -> None:
    if repo_url is None:
        await _aensure_workspace_root(
            sandbox=sandbox,
            workspace_path=workspace_path,
        )
        return

    await _arun_admin_code(
        sandbox=sandbox,
        phase="repo_clone",
        error_prefix="Daytona repo reconcile failure",
        code=_build_repo_reconcile_script(
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        ),
    )


def _build_repo_reconcile_script(
    *,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> str:
    return f"""
import json as _json
import pathlib as _pathlib
import shutil as _shutil
import subprocess as _subprocess

repo_url = {repo_url!r}
ref = {ref!r}
workspace_path = {workspace_path!r}
workspace = _pathlib.Path(workspace_path)
workspace.parent.mkdir(parents=True, exist_ok=True)

def _run(*args: str, check: bool = True):
    completed = _subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"command failed: {{' '.join(args)}}"
        )
    return completed

if not workspace.exists():
    _run("git", "clone", repo_url, workspace_path)
else:
    git_dir = workspace / ".git"
    if not git_dir.exists():
        if any(workspace.iterdir()):
            raise RuntimeError(
                f"workspace exists without git metadata: {{workspace_path}}"
            )
        _run("git", "clone", repo_url, workspace_path)
    else:
        remote = _run(
            "git",
            "-C",
            workspace_path,
            "remote",
            "get-url",
            "origin",
            check=False,
        )
        remote_url = remote.stdout.strip()
        if remote.returncode != 0 or remote_url != repo_url:
            _shutil.rmtree(workspace)
            _run("git", "clone", repo_url, workspace_path)
        else:
            _run("git", "-C", workspace_path, "fetch", "--all", "--tags", "--prune")

if ref:
    remote_ref = f"refs/remotes/origin/{{ref}}"
    remote_probe = _run(
        "git",
        "-C",
        workspace_path,
        "rev-parse",
        "--verify",
        remote_ref,
        check=False,
    )
    local_probe = _run(
        "git",
        "-C",
        workspace_path,
        "rev-parse",
        "--verify",
        ref,
        check=False,
    )
    if remote_probe.returncode == 0:
        branch_probe = _run(
            "git",
            "-C",
            workspace_path,
            "show-ref",
            "--verify",
            f"refs/heads/{{ref}}",
            check=False,
        )
        if branch_probe.returncode == 0:
            _run("git", "-C", workspace_path, "checkout", "--force", ref)
        else:
            _run(
                "git",
                "-C",
                workspace_path,
                "checkout",
                "--force",
                "-B",
                ref,
                remote_ref,
            )
        _run("git", "-C", workspace_path, "reset", "--hard", remote_ref)
    elif local_probe.returncode == 0:
        _run("git", "-C", workspace_path, "checkout", "--force", ref)
    else:
        raise RuntimeError(f"requested ref is not available: {{ref}}")

print(
    _json.dumps(
        {{
            "repo_url": repo_url,
            "ref": ref,
            "workspace_path": workspace_path,
        }},
        ensure_ascii=False,
    )
)
""".strip()
