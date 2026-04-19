"""Helpers for resetting and bootstrapping a Daytona volume wiki layout."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from dotenv import dotenv_values

from fleet_rlm.api.config import DEFAULT_SERVER_VOLUME_NAME
from fleet_rlm.integrations.config.runtime_settings import resolve_env_path
from fleet_rlm.integrations.daytona.async_compat import _await_if_needed
from fleet_rlm.integrations.daytona.config import resolve_daytona_config
from fleet_rlm.integrations.daytona.runtime_helpers import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    _aensure_daytona_volume_layout,
    _await_volume_ready,
    _build_daytona_client,
)
from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots
from fleet_rlm.utils.volume_tree import entry_name

DEFAULT_INVENTORY_DIR = Path.cwd() / ".data" / "daytona-volume-reset"
WIKI_FILE_TEMPLATES = {
    "SCHEMA.md": "schema.template.md",
    "index.md": "index.template.md",
    "log.md": "log.template.md",
}
WIKI_DIRECTORIES = (
    PurePosixPath("raw"),
    PurePosixPath("raw/articles"),
    PurePosixPath("raw/papers"),
    PurePosixPath("raw/transcripts"),
    PurePosixPath("raw/assets"),
    PurePosixPath("entities"),
    PurePosixPath("concepts"),
    PurePosixPath("comparisons"),
    PurePosixPath("queries"),
)


def expected_confirmation_token(volume_name: str) -> str:
    """Return the explicit token required for a destructive reset."""

    return f"RESET:{volume_name}"


def default_inventory_output_path(
    *,
    base_dir: Path = DEFAULT_INVENTORY_DIR,
    now: datetime | None = None,
) -> Path:
    """Return the default dry-run inventory report path."""

    current = now or datetime.now(timezone.utc)
    timestamp = current.strftime("%Y%m%dT%H%M%SZ")
    return base_dir / f"{timestamp}.json"


def load_runtime_env(*, repo_root: Path) -> dict[str, str]:
    """Resolve runtime env values from the repo root and process env."""

    env_path = resolve_env_path(start_paths=[repo_root, Path.cwd()])
    candidates = (env_path, env_path.with_name(".env.local"))

    file_values: dict[str, str] = {}
    for candidate in candidates:
        if not candidate.exists():
            continue
        for key, value in dotenv_values(candidate).items():
            if key and value is not None:
                file_values[str(key)] = str(value)

    # File-backed values provide local defaults, but explicit shell overrides
    # should always win for destructive/operator workflows.
    return file_values | {str(key): str(value) for key, value in os.environ.items()}


def resolve_volume_name(
    explicit_volume_name: str | None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the target Daytona volume from args or runtime configuration."""

    candidate = (explicit_volume_name or "").strip()
    if candidate:
        return candidate

    values = dict(env) if env is not None else {}
    return values.get("VOLUME_NAME", "").strip() or DEFAULT_SERVER_VOLUME_NAME


def load_template(template_dir: Path, name: str) -> str:
    """Load one wiki template file."""

    return (template_dir / name).read_text(encoding="utf-8")


def render_wiki_templates(
    *,
    template_dir: Path,
    wiki_domain: str,
    today: str,
) -> dict[str, str]:
    """Render seeded wiki files from templates."""

    replacements = {
        "__WIKI_DOMAIN__": wiki_domain,
        "__TODAY__": today,
    }
    rendered: dict[str, str] = {}
    for target_name, template_name in WIKI_FILE_TEMPLATES.items():
        content = load_template(template_dir, template_name)
        for key, value in replacements.items():
            content = content.replace(key, value)
        rendered[target_name] = content
    return rendered


def validate_bootstrap_request(
    *,
    volume_name: str,
    wiki_domain: str | None,
    dry_run: bool,
    confirm_reset: str | None,
) -> None:
    """Validate safety gates before any destructive action."""

    if dry_run:
        return

    expected_token = expected_confirmation_token(volume_name)
    if not confirm_reset:
        raise ValueError(
            f'Destructive reset requires --confirm-reset "{expected_token}".'
        )
    if confirm_reset != expected_token:
        raise ValueError(f'Confirmation token mismatch. Expected "{expected_token}".')
    if not (wiki_domain or "").strip():
        raise ValueError("Destructive reset requires --wiki-domain.")


@asynccontextmanager
async def amounted_existing_daytona_volume(volume_name: str) -> AsyncIterator[Any]:
    """Mount an existing Daytona volume into a temporary sandbox."""

    from daytona import CreateSandboxFromSnapshotParams, VolumeMount

    client = _build_daytona_client(resolve_daytona_config())
    volume = await _await_if_needed(client.volume.get(volume_name))
    volume = await _await_volume_ready(client, volume_name, volume)
    sandbox = await _await_if_needed(
        client.create(
            CreateSandboxFromSnapshotParams(
                language="python",
                volumes=[
                    VolumeMount(
                        volume_id=volume.id,
                        mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
                    )
                ],
            )
        )
    )
    try:
        yield sandbox
    finally:
        with suppress(Exception):
            await _await_if_needed(sandbox.delete())
        with suppress(Exception):
            await _await_if_needed(client.close())


def entry_display_path(parent_display_path: str, name: str) -> str:
    """Return a stable display path for a child entry."""

    if parent_display_path == "/":
        return f"/{name}"
    return f"{parent_display_path.rstrip('/')}/{name}"


def normalize_entry(entry: Any) -> tuple[str, bool]:
    """Extract a stable entry name and directory bit."""

    name = entry_name(getattr(entry, "name", "") or getattr(entry, "path", ""))
    return name, bool(getattr(entry, "is_dir", False))


def entry_modified_at(entry: Any) -> str | None:
    """Normalize an entry modification time to string form."""

    modified_at = getattr(entry, "mod_time", None)
    if hasattr(modified_at, "isoformat"):
        return modified_at.isoformat()
    if modified_at is None:
        return None
    return str(modified_at)


async def alist_volume_tree(
    fs: Any,
    *,
    mounted_path: PurePosixPath,
    display_path: str,
    max_depth: int,
    depth: int = 0,
) -> dict[str, Any]:
    """List a mounted Daytona subtree as a normalized dict tree."""

    entries = await _await_if_needed(fs.list_files(str(mounted_path)))
    children: list[dict[str, Any]] = []
    total_dirs = 0
    total_files = 0

    sorted_entries = sorted(
        entries,
        key=lambda item: entry_name(
            getattr(item, "name", "") or getattr(item, "path", "")
        ),
    )
    for entry in sorted_entries:
        child_node, child_dirs, child_files = await _abuild_tree_node(
            fs,
            entry=entry,
            mounted_path=mounted_path,
            display_path=display_path,
            max_depth=max_depth,
            depth=depth,
        )
        if child_node is None:
            continue
        children.append(child_node)
        total_dirs += child_dirs
        total_files += child_files

    return {
        "path": display_path,
        "children": children,
        "total_dirs": total_dirs,
        "total_files": total_files,
    }


async def _abuild_tree_node(
    fs: Any,
    *,
    entry: Any,
    mounted_path: PurePosixPath,
    display_path: str,
    max_depth: int,
    depth: int,
) -> tuple[dict[str, Any] | None, int, int]:
    name, is_dir = normalize_entry(entry)
    if not name:
        return None, 0, 0

    child_display_path = entry_display_path(display_path, name)
    child_mounted_path = mounted_path / name
    node: dict[str, Any] = {
        "name": name,
        "path": child_display_path,
        "type": "directory" if is_dir else "file",
    }
    modified_at = entry_modified_at(entry)
    if modified_at is not None:
        node["modified_at"] = modified_at
    if not is_dir:
        node["size"] = getattr(entry, "size", None)
        return node, 0, 1

    if depth + 1 >= max_depth:
        node["children"] = []
        node["truncated"] = True
        return node, 1, 0

    subtree = await alist_volume_tree(
        fs,
        mounted_path=child_mounted_path,
        display_path=child_display_path,
        max_depth=max_depth,
        depth=depth + 1,
    )
    node["children"] = subtree["children"]
    return node, 1 + subtree["total_dirs"], subtree["total_files"]


async def alist_root_entries(fs: Any) -> list[dict[str, Any]]:
    """List direct children of the mounted Daytona volume root."""

    entries = await _await_if_needed(
        fs.list_files(str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH))
    )
    resolved: list[dict[str, Any]] = []
    for entry in entries:
        name, is_dir = normalize_entry(entry)
        if not name:
            continue
        resolved.append(
            {
                "name": name,
                "display_path": f"/{name}",
                "mounted_path": str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH / name),
                "is_dir": is_dir,
            }
        )
    return sorted(resolved, key=lambda item: item["display_path"])


async def aensure_relative_directory(
    fs: Any,
    *,
    base_path: PurePosixPath,
    relative_path: PurePosixPath,
) -> None:
    """Create a relative directory tree under a mounted base path."""

    current_path = base_path
    for part in relative_path.parts:
        current_path = current_path / part
        try:
            await _await_if_needed(fs.create_folder(str(current_path), "755"))
        except Exception as exc:
            message = str(exc).lower()
            if "exist" in message or "already" in message:
                continue
            raise


async def aupload_text_file(
    fs: Any,
    *,
    path: PurePosixPath,
    content: str,
) -> None:
    """Upload one text file into the mounted volume."""

    await _await_if_needed(fs.upload_file(content.encode("utf-8"), str(path)))


async def areseed_wiki_subtree(
    fs: Any,
    *,
    template_dir: Path,
    wiki_domain: str,
    today: str,
) -> None:
    """Create the wiki subtree and seed the initial markdown files."""

    roots = mounted_storage_roots(str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH))
    memory_root = PurePosixPath(roots.memory_root)
    wiki_root = memory_root / "wiki"

    await aensure_relative_directory(
        fs,
        base_path=memory_root,
        relative_path=PurePosixPath("wiki"),
    )
    for relative_directory in WIKI_DIRECTORIES:
        await aensure_relative_directory(
            fs,
            base_path=wiki_root,
            relative_path=relative_directory,
        )

    rendered_files = render_wiki_templates(
        template_dir=template_dir,
        wiki_domain=wiki_domain,
        today=today,
    )
    for file_name, content in rendered_files.items():
        await aupload_text_file(
            fs,
            path=wiki_root / file_name,
            content=content,
        )


def build_rebuild_plan() -> dict[str, list[str]]:
    """Describe the deterministic rebuild layout after a confirmed reset."""

    wiki_directories = ["/memory/wiki"]
    wiki_directories.extend(
        f"/memory/wiki/{relative_dir.as_posix()}" for relative_dir in WIKI_DIRECTORIES
    )
    return {
        "durable_roots": ["/memory", "/artifacts", "/buffers", "/meta"],
        "wiki_directories": wiki_directories,
        "seeded_files": [
            "/memory/wiki/SCHEMA.md",
            "/memory/wiki/index.md",
            "/memory/wiki/log.md",
        ],
    }


async def aexecute_bootstrap(
    *,
    template_dir: Path,
    volume_name: str,
    wiki_domain: str | None,
    dry_run: bool,
    confirm_reset: str | None,
    inventory_out: Path,
    max_depth: int = 8,
) -> dict[str, Any]:
    """Inspect, optionally reset, and bootstrap an existing Daytona volume."""

    validate_bootstrap_request(
        volume_name=volume_name,
        wiki_domain=wiki_domain,
        dry_run=dry_run,
        confirm_reset=confirm_reset,
    )

    inventory_out.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()

    async with amounted_existing_daytona_volume(volume_name) as sandbox:
        root_entries = await alist_root_entries(sandbox.fs)
        tree = await alist_volume_tree(
            sandbox.fs,
            mounted_path=DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
            display_path="/",
            max_depth=max_depth,
        )

        deleted_paths = await _adelete_and_reseed(
            sandbox=sandbox,
            template_dir=template_dir,
            root_entries=root_entries,
            wiki_domain=wiki_domain,
            today=today,
            dry_run=dry_run,
        )

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "volume_name": volume_name,
        "mounted_root": str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
        "dry_run": dry_run,
        "wiki_domain": (wiki_domain or "").strip() or None,
        "confirmation": {
            "expected_token": expected_confirmation_token(volume_name),
            "provided_token": confirm_reset if not dry_run else None,
        },
        "inventory": {
            "top_level_paths": [entry["display_path"] for entry in root_entries],
            "total_dirs": tree["total_dirs"],
            "total_files": tree["total_files"],
            "tree": tree["children"],
        },
        "deletion_scope": [entry["display_path"] for entry in root_entries],
        "deleted_paths": deleted_paths,
        "rebuild_plan": build_rebuild_plan(),
        "inventory_report_path": str(inventory_out),
    }

    inventory_out.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


async def _adelete_and_reseed(
    *,
    sandbox: Any,
    template_dir: Path,
    root_entries: list[dict[str, Any]],
    wiki_domain: str | None,
    today: str,
    dry_run: bool,
) -> list[str]:
    if dry_run:
        return []

    deleted_paths: list[str] = []
    for entry in root_entries:
        await _await_if_needed(
            sandbox.fs.delete_file(entry["mounted_path"], recursive=True)
        )
        deleted_paths.append(entry["display_path"])

    await _aensure_daytona_volume_layout(
        sandbox=sandbox,
        mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
    )
    await areseed_wiki_subtree(
        sandbox.fs,
        template_dir=template_dir,
        wiki_domain=(wiki_domain or "").strip(),
        today=today,
    )
    return deleted_paths


def format_report_summary(report: Mapping[str, Any]) -> str:
    """Render a short operator-facing summary."""

    mode = "dry-run" if report.get("dry_run") else "reset"
    top_level = ", ".join(report["inventory"]["top_level_paths"]) or "<empty>"
    deleted = ", ".join(report.get("deleted_paths") or []) or "<none>"
    expected_token = report["confirmation"]["expected_token"]
    return "\n".join(
        [
            f"Daytona volume bootstrap {mode}: {report['volume_name']}",
            f"Mounted root: {report['mounted_root']}",
            f"Current top-level entries: {top_level}",
            (
                "Current counts: "
                f"{report['inventory']['total_dirs']} dirs, "
                f"{report['inventory']['total_files']} files"
            ),
            f"Deletion scope: {', '.join(report['deletion_scope']) or '<empty>'}",
            f"Deleted paths: {deleted}",
            f"Rebuild roots: {', '.join(report['rebuild_plan']['durable_roots'])}",
            f"Seeded wiki files: {', '.join(report['rebuild_plan']['seeded_files'])}",
            f"Expected confirmation token: {expected_token}",
            f"Inventory report: {report['inventory_report_path']}",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the operator script."""

    parser = argparse.ArgumentParser(
        description=(
            "Inspect, reset, and bootstrap an existing Daytona volume for the "
            "rlm-wiki plugin."
        )
    )
    parser.add_argument(
        "--volume-name",
        help="Existing Daytona volume name. Defaults to VOLUME_NAME or rlm-volume-dspy.",
    )
    parser.add_argument(
        "--wiki-domain",
        help="Domain description for the seeded wiki. Required for destructive runs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Inspect the volume, print the reset plan, and write an inventory "
            "report only."
        ),
    )
    parser.add_argument(
        "--confirm-reset",
        help="Explicit confirmation token required for destructive resets.",
    )
    parser.add_argument(
        "--inventory-out",
        type=Path,
        help=(
            "Optional JSON report output path. Defaults to "
            ".data/daytona-volume-reset/<timestamp>.json."
        ),
    )
    return parser
