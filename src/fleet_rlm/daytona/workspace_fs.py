"""Daytona-backed implementation of the Session Workspace port."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.files.workspace_models import WorkspaceEntry
from fleet_rlm.files.workspace_validation import normalize_workspace_path


def _is_not_found(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    return getattr(exc, "status_code", None) == 404


class DaytonaSessionWorkspaceFS:
    """Bind safe Session-relative text operations to one mounted Sandbox."""

    def __init__(
        self,
        sandbox: Any,
        *,
        volume_root: str,
        root: str,
        max_file_bytes: int,
    ) -> None:
        if max_file_bytes < 1:
            raise ValueError("workspace file bound must be positive")
        volume_path = PurePosixPath(volume_root)
        root_path = PurePosixPath(root)
        if (
            not volume_path.is_absolute()
            or not root_path.is_absolute()
            or ".." in volume_path.parts
            or ".." in root_path.parts
        ):
            raise ValueError("workspace root must be under trusted volume")
        try:
            root_path.relative_to(volume_path)
        except ValueError as exc:
            raise ValueError("workspace root must be under trusted volume") from exc
        self._sandbox = sandbox
        self._volume_root = str(volume_path)
        self._root = str(root_path)
        self._max_file_bytes = int(max_file_bytes)

    @property
    def root(self) -> str:
        return self._root

    def list_entries(self, path: str, *, limit: int = 100) -> tuple[WorkspaceEntry, ...]:
        relative = normalize_workspace_path(path, allow_root=True)
        if limit < 1 or limit > 100:
            raise ValueError("workspace list limit must be between 1 and 100")
        self._guard(relative, allow_missing=relative == ".")
        absolute = self._absolute(relative)
        info = self._info(absolute)
        if info is None:
            if relative == ".":
                return ()
            raise FileNotFoundError(relative)
        if not bool(getattr(info, "is_dir", False)):
            raise NotADirectoryError(relative)
        values = self._sandbox.fs.list_files(absolute, depth=1)
        entries = [self._entry_from_info(item, parent=relative) for item in values]
        return tuple(sorted(entries, key=lambda item: item.path)[:limit])

    def stat(self, path: str) -> WorkspaceEntry | None:
        relative = normalize_workspace_path(path, allow_root=True)
        self._guard(relative, allow_missing=True)
        info = self._info(self._absolute(relative))
        if info is None:
            if relative == ".":
                return WorkspaceEntry(".", "directory", None, None)
            return None
        return self._entry_from_info(info, exact=relative)

    def read_text(self, path: str, *, max_bytes: int) -> str:
        relative = normalize_workspace_path(path)
        bound = min(self._max_file_bytes, int(max_bytes))
        if bound < 1:
            raise ValueError("workspace read bound must be positive")
        self._guard(relative, allow_missing=False)
        absolute = self._absolute(relative)
        info = self._info(absolute)
        if info is None:
            raise FileNotFoundError(relative)
        if bool(getattr(info, "is_dir", False)):
            raise IsADirectoryError(relative)
        if int(getattr(info, "size", 0)) > bound:
            raise ValueError("workspace file exceeds read bound")
        raw = self._sandbox.fs.download_file(absolute)
        data = raw if isinstance(raw, bytes) else bytes(raw)
        if len(data) > bound:
            raise ValueError("workspace file exceeds read bound")
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("workspace file is not valid UTF-8") from exc

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        relative = normalize_workspace_path(path)
        if not isinstance(content, str):
            raise ValueError("workspace content must be text")
        data = content.encode("utf-8")
        if len(data) > self._max_file_bytes:
            raise ValueError("workspace file exceeds maximum size")
        self._guard(relative, allow_missing=True)
        absolute = self._absolute(relative)
        current = self._info(absolute)
        if current is not None:
            if bool(getattr(current, "is_dir", False)):
                raise IsADirectoryError(relative)
            if not overwrite:
                raise FileExistsError(relative)
        self._ensure_parents(relative)
        self._guard(relative, allow_missing=True)
        self._sandbox.fs.upload_file(data, absolute)
        info = self._info(absolute)
        if info is None:
            raise RuntimeError("workspace write did not create a file")
        return self._entry_from_info(info, exact=relative)

    def _absolute(self, relative: str) -> str:
        return self._root if relative == "." else str(PurePosixPath(self._root) / relative)

    def _info(self, absolute: str) -> Any | None:
        try:
            return self._sandbox.fs.get_file_info(absolute)
        except Exception as exc:  # noqa: BLE001 - SDK file-not-found is typed at runtime
            if _is_not_found(exc):
                return None
            raise

    def _ensure_parents(self, relative: str) -> None:
        root_info = self._info(self._root)
        if root_info is None:
            self._sandbox.fs.create_folder(self._root, "700")
        elif not bool(getattr(root_info, "is_dir", False)):
            raise NotADirectoryError(".")
        current = PurePosixPath(self._root)
        for part in PurePosixPath(relative).parent.parts:
            if part == ".":
                continue
            current /= part
            absolute = str(current)
            info = self._info(absolute)
            if info is None:
                self._sandbox.fs.create_folder(absolute, "700")
            elif not bool(getattr(info, "is_dir", False)):
                raise NotADirectoryError(str(current.relative_to(self._root)))

    def _entry_from_info(
        self,
        info: Any,
        *,
        exact: str | None = None,
        parent: str | None = None,
    ) -> WorkspaceEntry:
        if exact is not None:
            relative = exact
        else:
            name = str(getattr(info, "name", ""))
            relative = name if parent in {None, "."} else f"{parent}/{name}"
            relative = normalize_workspace_path(relative)
        is_dir = bool(getattr(info, "is_dir", False))
        modified_at = getattr(info, "modified_at", None) or getattr(info, "mod_time", None)
        return WorkspaceEntry(
            path=relative,
            kind="directory" if is_dir else "file",
            byte_size=None if is_dir else int(getattr(info, "size", 0)),
            modified_at=str(modified_at) if modified_at else None,
        )

    def _guard(self, relative: str, *, allow_missing: bool) -> None:
        target = self._absolute(relative)
        code = "\n".join(
            (
                "import json, os, stat",
                f"volume_root = {self._volume_root!r}",
                f"root = {self._root!r}",
                f"target = {target!r}",
                f"allow_missing = {allow_missing!r}",
                "safe = True",
                "reason = None",
                "try:",
                "    volume_real = os.path.realpath(volume_root)",
                "    target_real = os.path.realpath(target)",
                "    if os.path.commonpath([volume_root, root]) != volume_root:",
                "        safe, reason = False, 'root_escape'",
                "    elif os.path.commonpath([root, target]) != root:",
                "        safe, reason = False, 'root_escape'",
                "    elif os.path.commonpath([volume_real, target_real]) != volume_real:",
                "        safe, reason = False, 'root_escape'",
                "    else:",
                "        current = volume_root",
                "        relative_parts = os.path.relpath(target, volume_root).split(os.sep)",
                "        for part in ([] if relative_parts == ['.'] else relative_parts):",
                "            current = os.path.join(current, part)",
                "            if not os.path.lexists(current):",
                "                if allow_missing:",
                "                    break",
                "                safe, reason = False, 'missing'",
                "                break",
                "            if stat.S_ISLNK(os.lstat(current).st_mode):",
                "                safe, reason = False, 'symlink'",
                "                break",
                "except Exception:",
                "    safe, reason = False, 'validation_failed'",
                "print(json.dumps({'safe': safe, 'reason': reason}))",
            )
        )
        response = self._sandbox.process.code_run(code)
        if int(getattr(response, "exit_code", 1)) != 0:
            raise ValueError("workspace path is unsafe")
        try:
            payload = json.loads(str(getattr(response, "result", "")))
        except (TypeError, ValueError) as exc:
            raise ValueError("workspace path is unsafe") from exc
        if payload.get("safe") is not True:
            if payload.get("reason") == "missing" and not allow_missing:
                raise FileNotFoundError(relative)
            raise ValueError("workspace path is unsafe")
