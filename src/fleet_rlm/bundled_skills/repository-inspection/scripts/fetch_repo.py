"""Fetch and inspect a public GitHub repository via archive or shallow Git clone."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

_SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_KEY_FILENAMES = frozenset(
    {
        "README",
        "README.md",
        "README.rst",
        "README.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "LICENSE",
        "LICENSE.md",
    }
)
_CHUNK_BYTES = 65_536


def _require_pos_int(value: int, label: str) -> int:
    """Validate that value is a positive integer and not a bool."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def validate_github_repo_url(url: str) -> dict[str, str]:
    """Validate an HTTPS public GitHub repository URL and return owner, repo, and canonical URL."""
    if not isinstance(url, str) or not url or url != url.strip() or any(ch.isspace() for ch in url):
        raise ValueError("Repository URL must be a non-empty HTTPS GitHub URL without whitespace")
    if "?" in url or "#" in url or "\\" in url:
        raise ValueError("Repository URL must not contain query strings, fragments, or backslashes")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Repository URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.port is not None:
        raise ValueError("Repository URL must not include credentials or ports")
    if parsed.netloc.lower() != "github.com":
        raise ValueError("Repository URL host must be github.com")
    clean_path = parsed.path.rstrip("/")
    if not clean_path.startswith("/") or "//" in clean_path:
        raise ValueError("Repository URL path must be /OWNER/REPOSITORY")
    parts = clean_path.lstrip("/").split("/")
    if len(parts) != 2:
        raise ValueError("Repository URL must be in the form https://github.com/OWNER/REPOSITORY")
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    for label, part in (("owner", owner), ("repository", repo)):
        if part in {"", ".", ".."} or not _SEGMENT_RE.fullmatch(part):
            raise ValueError(f"Invalid GitHub {label} name: {part!r}")
    return {"owner": owner, "repo": repo, "url": f"https://github.com/{owner}/{repo}"}


def _validate_ref(ref: str, *, allow_range_chars: bool = False) -> str:
    """Validate a git ref or revision expression without permitting leading flags or wildcards."""
    if not isinstance(ref, str) or not ref or ref != ref.strip():
        raise ValueError("Revision or ref must be a non-empty string")
    if ref.startswith("-") or (not allow_range_chars and ref.startswith("refs/")) or ".." in ref or "@{" in ref:
        raise ValueError(f"Unsupported git revision or ref: {ref!r}")
    if any(ch.isspace() or ord(ch) < 32 or ch in "*?[]\\" for ch in ref):
        raise ValueError(f"Unsupported characters in git revision or ref: {ref!r}")
    return ref


def _validate_rel_repo_path(path: str | None) -> str | None:
    """Validate an optional repository-relative path filter without traversal or leading flags."""
    if path is None:
        return None
    if not isinstance(path, str) or not path or path.startswith(("-", "/")) or "\\" in path or "\x00" in path:
        raise ValueError(f"Invalid repository path filter: {path!r}")
    parts = PurePosixPath(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Invalid repository path filter: {path!r}")
    return PurePosixPath(*parts).as_posix()


def _git_env() -> dict[str, str]:
    """Return environment disabling interactive git credential prompts."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _run_git(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a non-interactive git subprocess list with prompts disabled."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
        env=_git_env(),
    )


def resolve_commit_sha(url: str, ref: str = "HEAD", timeout: int = 20) -> str:
    """Resolve a 40-hex commit SHA or branch ref from a public GitHub repository using git ls-remote."""
    info = validate_github_repo_url(url)
    clean_ref = _validate_ref(ref)
    if _SHA40_RE.fullmatch(clean_ref):
        return clean_ref.lower()
    _require_pos_int(timeout, "timeout")

    repo_url = f"{info['url']}.git"
    target_ref = "HEAD" if clean_ref == "HEAD" else f"refs/heads/{clean_ref}"
    proc = _run_git(["git", "ls-remote", "--exit-code", "--", repo_url, target_ref], timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        raise RuntimeError(f"Could not resolve {clean_ref!r} on {info['url']}: {detail or proc.returncode}")
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError(f"Expected exactly 1 ls-remote result for {target_ref!r}, got {len(lines)}")
    parts = lines[0].split(None, 1)
    if len(parts) != 2 or not _SHA40_RE.fullmatch(parts[0]) or parts[1].strip() != target_ref:
        raise RuntimeError(f"Unexpected ls-remote output for {target_ref!r}: {lines[0]!r}")
    return parts[0].lower()


def _inspect_and_extract_tar(
    archive_path: Path,
    temp_dir: Path,
    *,
    max_entries: int,
    max_unpacked_bytes: int,
) -> tuple[int, int, Path]:
    """Verify tar member bounds and extract safely using filter='data'."""
    with tarfile.open(archive_path, mode="r:gz") as tar:
        members = []
        top_levels: set[str] = set()
        unpacked_bytes = 0
        for member in tar:
            members.append(member)
            if len(members) > max_entries:
                raise ValueError(f"Archive entry count exceeds limit {max_entries}")
            name = member.name
            if not name or "\x00" in name or "\\" in name or name.startswith("/"):
                raise ValueError(f"Unsafe archive member path: {name!r}")
            parts = PurePosixPath(name).parts
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise ValueError(f"Archive member escapes root: {name!r}")
            if not (member.isreg() or member.isdir()):
                raise ValueError(f"Disallowed archive member type for {name!r}")
            top_levels.add(parts[0])
            if member.isreg():
                if member.size < 0:
                    raise ValueError(f"Negative member size for {name!r}")
                unpacked_bytes += member.size
                if unpacked_bytes > max_unpacked_bytes:
                    raise ValueError(f"Unpacked archive size exceeds limit {max_unpacked_bytes} bytes")

        if not members:
            raise ValueError("Downloaded archive is empty")
        if len(top_levels) != 1:
            raise ValueError(f"Expected a single top-level directory, found {sorted(top_levels)!r}")
        top_name = next(iter(top_levels))
        tar.extractall(path=temp_dir, filter="data")

    resolved_temp = temp_dir.resolve()
    candidate = temp_dir / top_name
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("Extracted repository top-level path is missing or a symbolic link")
    resolved_repo = candidate.resolve()
    if resolved_repo == resolved_temp or os.path.commonpath((str(resolved_temp), str(resolved_repo))) != str(
        resolved_temp
    ):
        raise ValueError("Extracted repository directory escapes temporary root")
    return len(members), unpacked_bytes, resolved_repo


def _require_archive_url(url: str) -> None:
    """Allow only HTTPS URLs on the codeload archive host."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or parsed.netloc.lower() != "codeload.github.com":
        raise ValueError(f"Archive response redirected to disallowed host: {url!r}")


class _ArchiveRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate every archive redirect hop against the allowlist before urllib follows it."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Refuse any hop outside the HTTPS codeload allowlist."""
        _require_archive_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_archive(
    owner: str,
    repo: str,
    sha: str,
    *,
    dest_root: str | Path = "/tmp",
    max_archive_bytes: int = 100 * 1024 * 1024,
    max_entries: int = 20_000,
    max_unpacked_bytes: int = 512 * 1024 * 1024,
    timeout: int = 60,
) -> dict[str, Any]:
    """Download and safely extract a commit-pinned GitHub tar.gz archive under a unique temp directory."""
    info = validate_github_repo_url(f"https://github.com/{owner}/{repo}")
    if not isinstance(sha, str) or not _SHA40_RE.fullmatch(sha):
        raise ValueError("sha must be a 40-character hexadecimal commit SHA")
    clean_sha = sha.lower()
    _require_pos_int(max_archive_bytes, "max_archive_bytes")
    _require_pos_int(max_entries, "max_entries")
    _require_pos_int(max_unpacked_bytes, "max_unpacked_bytes")
    _require_pos_int(timeout, "timeout")

    dest_path = Path(dest_root)
    dest_path.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="repo-", dir=str(dest_path)))
    archive_url = f"https://codeload.github.com/{info['owner']}/{info['repo']}/tar.gz/{clean_sha}"

    try:
        req = urllib.request.Request(archive_url, headers={"User-Agent": "fleet-rlm-repo-inspection/1.0"})
        archive_path = temp_dir / "archive.tar.gz"
        archive_bytes = 0
        opener = urllib.request.build_opener(_ArchiveRedirectHandler())
        with opener.open(req, timeout=timeout) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else archive_url
            _require_archive_url(final_url)
            headers = getattr(response, "headers", None)
            raw_len = headers.get("Content-Length") if headers is not None else None
            if raw_len is not None:
                try:
                    declared = int(raw_len)
                except ValueError as exc:
                    raise ValueError(f"Invalid Content-Length header: {raw_len!r}") from exc
                if declared > max_archive_bytes:
                    raise ValueError(f"Declared archive size {declared} exceeds limit {max_archive_bytes}")
            with archive_path.open("wb") as handle:
                while chunk := response.read(_CHUNK_BYTES):
                    archive_bytes += len(chunk)
                    if archive_bytes > max_archive_bytes:
                        raise ValueError(f"Streamed archive size exceeds limit {max_archive_bytes} bytes")
                    handle.write(chunk)

        entry_count, unpacked_bytes, resolved_repo = _inspect_and_extract_tar(
            archive_path,
            temp_dir,
            max_entries=max_entries,
            max_unpacked_bytes=max_unpacked_bytes,
        )
        archive_path.unlink(missing_ok=True)
        return {
            "owner": info["owner"],
            "repo": info["repo"],
            "sha": clean_sha,
            "temp_dir": str(temp_dir.resolve()),
            "repo_dir": str(resolved_repo),
            "archive_bytes": archive_bytes,
            "entry_count": entry_count,
            "unpacked_bytes": unpacked_bytes,
        }
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def clone_shallow(
    url: str,
    *,
    ref: str = "HEAD",
    depth: int = 50,
    dest_root: str | Path = "/tmp",
    timeout: int = 60,
) -> dict[str, Any]:
    """Create a shallow Git clone in temporary storage for commit history, diff, or blame tasks."""
    info = validate_github_repo_url(url)
    clean_ref = _validate_ref(ref)
    _require_pos_int(depth, "depth")
    _require_pos_int(timeout, "timeout")

    dest_path = Path(dest_root)
    dest_path.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="repo-git-", dir=str(dest_path)))
    repo_url = f"{info['url']}.git"
    git_opts = ["-c", "protocol.version=2", "-c", "core.hooksPath=/dev/null", "-c", "advice.detachedHead=false"]

    try:
        if _SHA40_RE.fullmatch(clean_ref):
            clean_sha = clean_ref.lower()
            steps = [
                ["git", *git_opts, "init", "--", str(temp_dir)],
                [
                    "git",
                    *git_opts,
                    "-C",
                    str(temp_dir),
                    "fetch",
                    "--depth",
                    str(depth),
                    "--no-tags",
                    "--",
                    repo_url,
                    clean_sha,
                ],
                ["git", *git_opts, "-C", str(temp_dir), "checkout", "--detach", "FETCH_HEAD"],
            ]
        else:
            cmd = [
                "git",
                *git_opts,
                "clone",
                "--depth",
                str(depth),
                "--no-tags",
                "--single-branch",
            ]
            if clean_ref != "HEAD":
                cmd.extend(["--branch", clean_ref])
            cmd.extend(["--", repo_url, str(temp_dir)])
            steps = [cmd]

        for step in steps:
            proc = _run_git(step, timeout=timeout)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout).strip()[:300]
                raise RuntimeError(f"Git clone failed for {info['url']} ({clean_ref}): {detail or proc.returncode}")

        head_proc = _run_git(["git", "-C", str(temp_dir), "rev-parse", "--verify", "HEAD"], timeout=timeout)
        head_sha = head_proc.stdout.strip().lower()
        if head_proc.returncode != 0 or not _SHA40_RE.fullmatch(head_sha):
            raise RuntimeError(f"Could not verify HEAD after cloning {info['url']}")
        if _SHA40_RE.fullmatch(clean_ref) and head_sha != clean_ref.lower():
            raise RuntimeError(f"Cloned HEAD {head_sha} does not match requested SHA {clean_ref.lower()}")

        return {
            "owner": info["owner"],
            "repo": info["repo"],
            "ref": ref,
            "temp_dir": str(temp_dir.resolve()),
            "repo_dir": str(temp_dir.resolve()),
            "sha": head_sha,
            "depth": depth,
            "mode": "git",
        }
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _require_git_repo(repo_dir: str | Path) -> Path:
    """Verify repo_dir exists and is a directory without following symlinks."""
    root = Path(repo_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"Git repository directory is unavailable: {repo_dir!s}")
    return root.resolve()


def git_log(
    repo_dir: str | Path,
    *,
    max_count: int = 20,
    path: str | None = None,
    timeout: int = 30,
) -> list[dict[str, str]]:
    """Return structured commit records from a cloned repository's git log."""
    root = _require_git_repo(repo_dir)
    _require_pos_int(max_count, "max_count")
    _require_pos_int(timeout, "timeout")
    rel_path = _validate_rel_repo_path(path)

    cmd = ["git", "-C", str(root), "log", "-z", "-n", str(max_count), "--format=%H%x09%an%x09%aI%x09%s"]
    if rel_path is not None:
        cmd.extend(["--", rel_path])
    proc = _run_git(cmd, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        raise RuntimeError(f"git log failed: {detail or proc.returncode}")

    records: list[dict[str, str]] = []
    for entry in proc.stdout.split("\x00"):
        if not entry:
            continue
        fields = entry.split("\t", 3)
        if len(fields) != 4:
            continue
        sha, author, date, subject = fields
        records.append({"sha": sha, "author": author, "date": date, "subject": subject})
    return records


def _run_git_bounded(cmd: list[str], *, timeout: int, max_chars: int) -> tuple[str, int]:
    """Drain output in fixed chunks, retaining a bounded prefix and enforcing the deadline."""
    with (
        tempfile.TemporaryFile(mode="w+t") as errors,
        subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=errors, text=True, stdin=subprocess.DEVNULL, env=_git_env()
        ) as proc,
    ):
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        try:
            assert proc.stdout is not None
            chunks = []
            retained = total = 0
            while chunk := proc.stdout.read(65536):
                total += len(chunk)
                prefix = chunk[: max_chars - retained]
                if prefix:
                    chunks.append(prefix)
                retained += len(prefix)
            proc.wait()
            if proc.returncode:
                errors.seek(0)
                raise RuntimeError(f"git diff failed: {errors.read(300) or proc.returncode}")
            return "".join(chunks), total
        finally:
            timer.cancel()


def git_diff(
    repo_dir: str | Path,
    rev_a: str,
    rev_b: str = "HEAD",
    *,
    path: str | None = None,
    max_chars: int = 20_000,
    timeout: int = 30,
) -> dict[str, Any]:
    """Return bounded git diff and --stat output between two validated revisions."""
    root = _require_git_repo(repo_dir)
    clean_a = _validate_ref(rev_a, allow_range_chars=True)
    clean_b = _validate_ref(rev_b, allow_range_chars=True)
    _require_pos_int(max_chars, "max_chars")
    _require_pos_int(timeout, "timeout")
    rel_path = _validate_rel_repo_path(path)

    suffix = ["--", rel_path] if rel_path is not None else []
    stat_text, _ = _run_git_bounded(
        ["git", "-C", str(root), "diff", "--stat", clean_a, clean_b, *suffix],
        timeout=timeout,
        max_chars=max_chars,
    )
    raw_diff, total_chars = _run_git_bounded(
        ["git", "-C", str(root), "diff", clean_a, clean_b, *suffix],
        timeout=timeout,
        max_chars=max_chars,
    )
    return {
        "rev_a": clean_a,
        "rev_b": clean_b,
        "path": rel_path,
        "stat": stat_text.strip(),
        "diff": raw_diff[:max_chars],
        "truncated": total_chars > max_chars,
        "total_chars": total_chars,
    }


def inventory_repo(repo_dir: str | Path, *, max_files: int = 500) -> dict[str, Any]:
    """Build a bounded read-only file inventory for an extracted or cloned repository directory."""
    _require_pos_int(max_files, "max_files")
    root = Path(repo_dir)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"Repository directory is unavailable: {repo_dir!s}")
    resolved_root = root.resolve()

    top_level = sorted(
        entry.name for entry in resolved_root.iterdir() if entry.name != ".git" and not entry.is_symlink()
    )
    total_files = 0
    total_bytes = 0
    extensions: Counter[str] = Counter()
    key_files: list[str] = []
    files: list[str] = []

    for base, dirs, filenames in os.walk(resolved_root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != ".git" and not (Path(base) / d).is_symlink())
        for filename in sorted(filenames):
            candidate = Path(base) / filename
            if candidate.is_symlink() or not candidate.is_file():
                continue
            rel = candidate.relative_to(resolved_root).as_posix()
            total_files += 1
            total_bytes += candidate.stat().st_size
            ext = candidate.suffix.lower() or "(no_ext)"
            extensions[ext] += 1
            if len(PurePosixPath(rel).parts) <= 2 and filename in _KEY_FILENAMES:
                key_files.append(rel)
            if len(files) < max_files:
                files.append(rel)

    return {
        "repo_dir": str(resolved_root),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "truncated": total_files > max_files,
        "top_level": top_level,
        "extensions": dict(extensions.most_common(15)),
        "key_files": key_files,
        "files": files,
    }


def cleanup_repo(repo_dir: str | Path) -> dict[str, Any]:
    """Remove exactly the supplied directory.

    Pass acquisition temp_dir to remove the full acquisition, or an archive's
    repo_dir to remove only its extracted child. Never remove a parent implicitly.
    """
    cleanup_target = Path(repo_dir)
    resolved = cleanup_target.resolve()
    protected = {Path("/").resolve(), Path("/tmp").resolve(), Path("/workspace").resolve(), Path.home().resolve()}
    if resolved in protected or len(resolved.parts) <= 2:
        raise ValueError(f"Refusing to remove protected directory: {resolved}")
    if not cleanup_target.exists():
        return {"removed": False, "path": str(resolved)}
    shutil.rmtree(cleanup_target)
    return {"removed": True, "path": str(resolved)}


def run(url: str, ref: str = "HEAD", *, keep: bool = True, mode: str = "archive", depth: int = 50) -> dict[str, Any]:
    """Acquire and inventory a public GitHub repository via archive (default) or shallow git clone."""
    if mode not in {"archive", "git"}:
        raise ValueError(f"Unsupported acquisition mode: {mode!r}; expected 'archive' or 'git'")
    info = validate_github_repo_url(url)
    if mode == "git":
        fetched = clone_shallow(info["url"], ref=ref, depth=depth)
    else:
        sha = resolve_commit_sha(info["url"], ref=ref)
        fetched = fetch_archive(info["owner"], info["repo"], sha)
    try:
        inventory = inventory_repo(fetched["repo_dir"])
    except Exception:
        cleanup_repo(fetched["temp_dir"])
        raise
    cleaned_up = False
    if not keep:
        cleanup_repo(fetched["temp_dir"])
        cleaned_up = True
    return {
        **fetched,
        "ref": ref,
        "cleaned_up": cleaned_up,
        "inventory": inventory,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for repository archive/git acquisition, log, and diff inspection."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if raw_argv and raw_argv[0] == "git-log":
            sub = argparse.ArgumentParser(prog="fetch_repo.py git-log")
            sub.add_argument("repo_dir", help="Cloned repository directory")
            sub.add_argument("--max-count", type=int, default=20, help="Maximum commits to return (default: 20)")
            sub.add_argument("--path", default=None, help="Optional relative file/dir path filter")
            parsed = sub.parse_args(raw_argv[1:])
            result: Any = git_log(parsed.repo_dir, max_count=parsed.max_count, path=parsed.path)
        elif raw_argv and raw_argv[0] == "git-diff":
            sub = argparse.ArgumentParser(prog="fetch_repo.py git-diff")
            sub.add_argument("repo_dir", help="Cloned repository directory")
            sub.add_argument("rev_a", help="Base revision")
            sub.add_argument("rev_b", nargs="?", default="HEAD", help="Target revision (default: HEAD)")
            sub.add_argument("--path", default=None, help="Optional relative file/dir path filter")
            sub.add_argument("--max-chars", type=int, default=20_000, help="Max diff characters (default: 20000)")
            parsed = sub.parse_args(raw_argv[1:])
            result = git_diff(
                parsed.repo_dir,
                parsed.rev_a,
                parsed.rev_b,
                path=parsed.path,
                max_chars=parsed.max_chars,
            )
        else:
            parser = argparse.ArgumentParser(description="Inspect a public GitHub repository safely.")
            parser.add_argument("url", help="Public GitHub HTTPS URL (https://github.com/OWNER/REPOSITORY)")
            parser.add_argument("--ref", default="HEAD", help="Branch name or 40-char SHA (default: HEAD)")
            parser.add_argument(
                "--mode",
                choices=("archive", "git"),
                default="archive",
                help="Acquisition mode: fast codeload archive (default) or shallow git clone",
            )
            parser.add_argument(
                "--depth", type=int, default=50, help="Shallow clone depth when --mode git (default: 50)"
            )
            parser.add_argument("--dest-root", default="/tmp", help="Parent directory for temp storage (default: /tmp)")
            parser.add_argument("--max-files", type=int, default=500, help="Maximum file paths to list (default: 500)")
            parser.add_argument("--no-keep", action="store_true", help="Clean up temp dir immediately after inventory")
            parsed = parser.parse_args(raw_argv)

            info = validate_github_repo_url(parsed.url)
            if parsed.mode == "git":
                fetched = clone_shallow(info["url"], ref=parsed.ref, depth=parsed.depth, dest_root=parsed.dest_root)
            else:
                sha = resolve_commit_sha(info["url"], ref=parsed.ref)
                fetched = fetch_archive(info["owner"], info["repo"], sha, dest_root=parsed.dest_root)
            try:
                inventory = inventory_repo(fetched["repo_dir"], max_files=parsed.max_files)
            except Exception:
                cleanup_repo(fetched["temp_dir"])
                raise
            cleaned_up = False
            if parsed.no_keep:
                cleanup_repo(fetched["temp_dir"])
                cleaned_up = True
            result = {**fetched, "ref": parsed.ref, "cleaned_up": cleaned_up, "inventory": inventory}

        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
