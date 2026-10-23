"""Local and cached workspace @mentions for the Textual composer."""

from __future__ import annotations

import base64
import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from fleet_rlm.tui.commands import Context
from fleet_rlm.tui.local_paths import iter_local_files, search_local_paths

_MAX_DIRECTORY_FILES = 1_000
_MAX_DIRECTORY_BYTES = 100_000_000

# A mention starts at a word boundary, not inside an email address. Double-quoted
# paths use JSON escaping so whitespace and quotes round-trip without shell rules.
_MENTION = re.compile(r"""(?<!\S)@(?P<path>"(?:\\.|[^"\\\n])*"?|[^\s"',;!?()\[\]{}<>]*)""")


def _decode_path(raw: str, *, partial: bool = False) -> str:
    """Decode a bare or JSON-quoted mention path.

    Raise ValueError for an invalid quoted path unless `partial` permits returning the
    unfinished text after its opening quote.
    """
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except ValueError:
            if partial:
                return raw[1:]
            raise ValueError("Close the quoted @file path before submitting") from None
        return value
    return raw


def format_mention(path: str) -> str:
    """Quote paths that cannot be represented as a single bare mention."""
    if any(character.isspace() or character in "\"'\\,;!?()[]{}<>" for character in path):
        path = json.dumps(path, ensure_ascii=False)
    return f"@{path}"


def mention_completions(text: str, cursor: int, workspace_files: list[str]) -> list[tuple[str, str, str]]:
    """Complete local files/folders and cached workspace paths at the cursor.

    Return `(replacement_text, display_mention, description)` items. Replacements cover
    the whole active mention while preserving surrounding prose. Bare fragments also
    search nested basenames. Local directory-read errors are ignored; completion uploads nothing.
    """
    active = next((match for match in _MENTION.finditer(text[:cursor]) if match.end() == cursor), None)
    if active is None:
        return []
    fragment = _decode_path(active["path"], partial=True)
    full = _MENTION.match(text, active.start())
    assert full is not None
    before, after = text[: active.start()], text[full.end() :]
    items: list[tuple[str, str, str]] = []

    def add(path: str, description: str, *, directory: bool = False) -> None:
        """Append a formatted mention replacement while preserving text after the active mention."""
        mention = format_mention(path)
        separator = "" if directory or after[:1].isspace() else " "
        items.append((before + mention + separator + after, mention, description))

    for path in workspace_files:
        if path.startswith(fragment):
            add(path, "workspace file")

    directory, _, prefix = fragment.rpartition("/")
    display_directory = f"{directory}/" if "/" in fragment else "./"
    if not display_directory.startswith(("./", "../", "/", "~/")):
        display_directory = "./" + display_directory
    parent = Path(display_directory).expanduser()
    seen: set[str] = set()

    def add_local(path: str) -> None:
        """Add distinct local file or folder choices, skipping symlinks and missing paths."""
        if path in seen:
            return
        seen.add(path)
        local = Path(path).expanduser()
        if local.is_symlink():
            return
        if local.is_dir():
            add(path, "local folder — attach on submit")
            add(path + "/", "local folder — browse", directory=True)
        elif local.is_file():
            add(path, "local file — attach on submit")

    try:
        for entry in sorted(parent.iterdir(), key=lambda entry: entry.name.casefold()):
            if entry.name.startswith(prefix) and (not entry.name.startswith(".") or prefix.startswith(".")):
                add_local(display_directory + entry.name)
        if fragment and "/" not in fragment and not fragment.startswith("."):
            for relative in search_local_paths(fragment):
                add_local("./" + relative.as_posix())
    except OSError:
        pass  # Unreadable or disappearing directories are not completion failures.
    return items


def completed_mention_cursor(text: str, cursor: int, completed: str) -> int:
    """Keep the cursor after the chosen mention rather than after unrelated trailing prose."""
    active = next((match for match in _MENTION.finditer(text) if match.start() < cursor <= match.end()), None)
    end = len(completed) - (len(text) - active.end()) if active is not None else len(completed)
    # Keep typing inside quoted folder paths after choosing a folder with spaces.
    return end - 1 if completed[:end].endswith('/"') else end


def _directory_upload_plan(
    directories: list[Path], cancelled: Callable[[], bool]
) -> tuple[dict[Path, str], dict[Path, str]]:
    """Map local directories and eligible files to workspace destinations, sharing overlapping roots.

    Refuse empty selections or totals above 1,000 files or 100,000,000 bytes with ValueError.
    Cancellation returns a partial plan. Filesystem and Git-discovery errors propagate;
    this function performs no uploads.
    """
    roots: dict[Path, str] = {}
    files: dict[Path, str] = {}
    total_bytes = 0
    for directory in sorted(set(directories), key=lambda path: (len(path.parts), str(path))):
        if cancelled():
            break
        ancestor = next((root for root in roots if directory.is_relative_to(root)), None)
        if ancestor is not None:
            roots[directory] = roots[ancestor] + "/" + directory.relative_to(ancestor).as_posix()
            if not any(path.is_relative_to(directory) for path in files):
                raise ValueError(f"No eligible files in directory: {directory}")
            continue
        roots[directory] = f"files/mention-{uuid.uuid4().hex}/{directory.name}"
        found = False
        for relative in iter_local_files(directory):
            if cancelled():
                return roots, files
            found = True
            source = directory / relative
            files[source] = roots[directory] + "/" + relative.as_posix()
            total_bytes += source.stat().st_size
            if len(files) > _MAX_DIRECTORY_FILES or total_bytes > _MAX_DIRECTORY_BYTES:
                raise ValueError("Directory attachments are limited to 1,000 files and 100 MB per submission")
        if not found:
            raise ValueError(f"No eligible files in directory: {directory}")
    return roots, files


def attach_local_mentions(ctx: Context, text: str, *, cancelled: Callable[[], bool]) -> str:
    """Upload local file mentions once per submission and rewrite them to workspace paths.

    Cached workspace mentions are not uploaded. Directories retain their structure
    under a unique workspace root, excluding ignored/hidden files and symlinks.
    Overlapping directory and file mentions share uploads within a submission.

    Successful uploads update `ctx.state.workspace_files`. Cancellation returns the
    original text, retaining any uploads already completed. Directory attachments are
    limited to 1,000 files and 100,000,000 bytes across the submission.

    Raises:
        ValueError: A quoted path is invalid, an explicit local path is missing,
            an attachment path contains a symlink, directory discovery fails, or a directory
            is empty or exceeds its limits.

    Filesystem, HTTP, and malformed upload-response errors propagate. Completed
    uploads are retained if a later upload fails.
    """
    mentions: list[tuple[int, int, Path]] = []
    for match in _MENTION.finditer(text):
        raw = match["path"]
        if not raw:
            continue
        path_text = _decode_path(raw)
        if path_text in ctx.state.workspace_files:
            continue
        path = Path(path_text).expanduser()
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not attached: {path_text}")
        if path.is_file() or path.is_dir():
            mentions.append((match.start(), match.end(), path.resolve()))
        elif path_text.startswith(("./", "../", "/", "~/")):
            raise ValueError(f"Local file not found: {path_text}")

    roots, directory_files = _directory_upload_plan(
        [path for _start, _end, path in mentions if path.is_dir()], cancelled
    )
    uploaded: dict[Path, str] = {}
    for source, remote in directory_files.items():
        if cancelled():
            return text
        if source.is_symlink() or any(parent.is_symlink() for parent in source.parents):
            raise ValueError(f"Symbolic links are not attached: {source}")
        content = base64.b64encode(source.read_bytes()).decode("ascii")
        ctx.put(f"{ctx.session}/workspace/files/{quote(remote, safe='/')}", json={"content_base64": content})
        uploaded[source] = remote
        if remote not in ctx.state.workspace_files:
            ctx.state.workspace_files.append(remote)
    uploaded.update(roots)
    replacements: list[tuple[int, int, str]] = []
    for start, end, path in mentions:
        if cancelled():
            return text
        if path not in uploaded:
            name = f"mention-{uuid.uuid4().hex}-{path.name}"
            with path.open("rb") as handle:
                payload = ctx.post(f"{ctx.session}/attach", files={"file": (name, handle)}).json()
            remote = str(payload["rel_path"])
            uploaded[path] = remote
            if remote not in ctx.state.workspace_files:
                ctx.state.workspace_files.append(remote)
        replacements.append((start, end, format_mention(uploaded[path])))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text
