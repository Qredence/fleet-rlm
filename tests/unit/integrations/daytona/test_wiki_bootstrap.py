from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.daytona import wiki_bootstrap


class _FakeFs:
    def __init__(self, tree: dict[str, list[object]]) -> None:
        self.tree = tree
        self.delete_calls: list[tuple[str, bool]] = []
        self.create_calls: list[tuple[str, str]] = []
        self.upload_calls: list[tuple[str, bytes]] = []

    def list_files(self, path: str) -> list[object]:
        return self.tree.get(path, [])

    def delete_file(self, path: str, recursive: bool = False) -> None:
        self.delete_calls.append((path, recursive))

    def create_folder(self, path: str, mode: str) -> None:
        self.create_calls.append((path, mode))

    def upload_file(self, data: bytes, path: str) -> None:
        self.upload_calls.append((path, data))


def _entry(name: str, *, is_dir: bool, size: int | None = None) -> object:
    return SimpleNamespace(name=name, is_dir=is_dir, size=size, mod_time=None)


def _fake_tree() -> dict[str, list[object]]:
    root = str(wiki_bootstrap.DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    return {
        root: [
            _entry("artifacts", is_dir=True),
            _entry("buffers", is_dir=True),
            _entry("memory", is_dir=True),
            _entry("meta", is_dir=True),
        ],
        f"{root}/artifacts": [_entry("trace.txt", is_dir=False, size=9)],
        f"{root}/buffers": [],
        f"{root}/memory": [],
        f"{root}/meta": [_entry("workspaces", is_dir=True)],
        f"{root}/meta/workspaces": [_entry("hash-a", is_dir=True)],
        f"{root}/meta/workspaces/hash-a": [
            _entry("react-session-1.json", is_dir=False, size=7)
        ],
    }


def test_validate_bootstrap_request_requires_confirmation_token() -> None:
    with pytest.raises(ValueError, match="confirm-reset"):
        wiki_bootstrap.validate_bootstrap_request(
            volume_name="rlm-volume-dspy",
            wiki_domain="Agent infra",
            dry_run=False,
            confirm_reset=None,
        )


def test_validate_bootstrap_request_requires_wiki_domain_for_real_reset() -> None:
    with pytest.raises(ValueError, match="wiki-domain"):
        wiki_bootstrap.validate_bootstrap_request(
            volume_name="rlm-volume-dspy",
            wiki_domain=None,
            dry_run=False,
            confirm_reset=wiki_bootstrap.expected_confirmation_token("rlm-volume-dspy"),
        )


def test_dry_run_writes_inventory_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fs = _FakeFs(_fake_tree())

    @asynccontextmanager
    async def _fake_mounted_volume(volume_name: str):
        assert volume_name == "tenant-a"
        yield SimpleNamespace(fs=fake_fs)

    monkeypatch.setattr(
        wiki_bootstrap,
        "amounted_existing_daytona_volume",
        _fake_mounted_volume,
    )

    report_path = tmp_path / "inventory.json"
    report = asyncio.run(
        wiki_bootstrap.aexecute_bootstrap(
            template_dir=Path("plugins/rlm-wiki/references"),
            volume_name="tenant-a",
            wiki_domain=None,
            dry_run=True,
            confirm_reset=None,
            inventory_out=report_path,
        )
    )

    assert report["dry_run"] is True
    assert report["inventory"]["top_level_paths"] == [
        "/artifacts",
        "/buffers",
        "/memory",
        "/meta",
    ]
    assert report["inventory"]["total_files"] == 2
    assert report["inventory"]["total_dirs"] == 6
    assert report["deletion_scope"] == ["/artifacts", "/buffers", "/memory", "/meta"]
    assert report_path.exists()
    saved = report_path.read_text(encoding="utf-8")
    assert '"volume_name": "tenant-a"' in saved
    assert "/meta/workspaces/hash-a" in saved
    assert fake_fs.delete_calls == []


def test_real_reset_recreates_roots_and_wiki_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fs = _FakeFs(_fake_tree())

    @asynccontextmanager
    async def _fake_mounted_volume(volume_name: str):
        assert volume_name == "tenant-a"
        yield SimpleNamespace(fs=fake_fs)

    monkeypatch.setattr(
        wiki_bootstrap,
        "amounted_existing_daytona_volume",
        _fake_mounted_volume,
    )

    report = asyncio.run(
        wiki_bootstrap.aexecute_bootstrap(
            template_dir=Path("plugins/rlm-wiki/references"),
            volume_name="tenant-a",
            wiki_domain="AI agent infrastructure",
            dry_run=False,
            confirm_reset=wiki_bootstrap.expected_confirmation_token("tenant-a"),
            inventory_out=tmp_path / "reset-report.json",
        )
    )

    mounted_root = str(wiki_bootstrap.DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    assert fake_fs.delete_calls == [
        (f"{mounted_root}/artifacts", True),
        (f"{mounted_root}/buffers", True),
        (f"{mounted_root}/memory", True),
        (f"{mounted_root}/meta", True),
    ]
    created_paths = [path for path, _mode in fake_fs.create_calls]
    assert f"{mounted_root}/memory" in created_paths
    assert f"{mounted_root}/artifacts" in created_paths
    assert f"{mounted_root}/buffers" in created_paths
    assert f"{mounted_root}/meta" in created_paths
    assert f"{mounted_root}/memory/wiki" in created_paths
    assert f"{mounted_root}/memory/wiki/raw/articles" in created_paths
    assert f"{mounted_root}/memory/wiki/queries" in created_paths
    assert not any(
        path.startswith(f"{mounted_root}/meta/workspaces") for path in created_paths
    )

    uploaded_paths = [path for path, _data in fake_fs.upload_calls]
    assert uploaded_paths == [
        f"{mounted_root}/memory/wiki/SCHEMA.md",
        f"{mounted_root}/memory/wiki/index.md",
        f"{mounted_root}/memory/wiki/log.md",
    ]
    schema_text = fake_fs.upload_calls[0][1].decode("utf-8")
    assert "AI agent infrastructure" in schema_text
    assert report["deleted_paths"] == ["/artifacts", "/buffers", "/memory", "/meta"]
