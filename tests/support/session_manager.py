"""Shared scenario setup; not a collected test module."""

from __future__ import annotations

from typing import Any

from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec, VolumeConfig
from fleet_rlm.daytona.session_manager import (
    DaytonaAdmission,
    DaytonaSessionManager,
)
from fleet_rlm.runtime.bindings import InMemorySandboxBindingStore as InMemoryBindingStore

_SPEC = DaytonaSandboxSpec("fleet-test-v1")


class _FakeVolume:
    def __init__(self, volume_id: str = "vol-1") -> None:
        self.id = volume_id


class _FakeVolumeClient:
    def __init__(self) -> None:
        self.gets: list[tuple[str, bool]] = []
        self.failures: list[BaseException] = []

    async def get(self, name: str, *, create: bool = False) -> _FakeVolume:
        self.gets.append((name, create))
        if self.failures:
            raise self.failures.pop(0)
        return _FakeVolume(f"vol-{name}")


class _FakeFileInfo:
    def __init__(self, *, is_dir: bool) -> None:
        self.is_dir = is_dir


class _FakeFilesystem:
    def __init__(self, mount_path: str | None) -> None:
        self.directories = {mount_path} if mount_path else set()
        self.files: set[str] = set()
        self.uploaded: dict[str, bytes] = {}
        self.created: list[tuple[str, str]] = []
        self.info_failures: dict[str, BaseException] = {}

    async def get_file_info(self, path: str) -> _FakeFileInfo:
        failure = self.info_failures.pop(path, None)
        if failure is not None:
            raise failure
        if path in self.directories:
            return _FakeFileInfo(is_dir=True)
        if path in self.files:
            return _FakeFileInfo(is_dir=False)
        raise FileNotFoundError(path)

    async def create_folder(self, path: str, mode: str) -> None:
        self.directories.add(path)
        self.created.append((path, mode))

    async def upload_file(self, data: bytes, path: str) -> None:
        self.files.add(path)
        self.uploaded[path] = bytes(data)


class _FakeSandbox:
    def __init__(
        self,
        sandbox_id: str,
        state: str = "running",
        *,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
        labels: dict[str, str] | None = None,
        snapshot: str = _SPEC.snapshot,
    ) -> None:
        self.id = sandbox_id
        self.state = state
        self.ops: list[str] = []
        self.backend = None  # interpreter will tolerate missing backend until execute
        self.volume_id = volume_id
        self.mount_path = mount_path
        self.volume_subpath = volume_subpath
        self.labels = labels or {}
        self.snapshot = snapshot
        self.fs = _FakeFilesystem(mount_path)
        self.volumes = (
            [
                {
                    "volume_id": volume_id,
                    "mount_path": mount_path,
                    "subpath": volume_subpath,
                }
            ]
            if volume_id and mount_path and volume_subpath
            else []
        )

    def start(self) -> None:
        self.ops.append("start")
        self.state = "running"

    def stop(self) -> None:
        self.ops.append("stop")
        self.state = "stopped"

    def pause(self) -> None:
        self.ops.append("pause")
        self.state = "paused"

    def resume(self) -> None:
        self.ops.append("resume")
        self.state = "running"

    def archive(self) -> None:
        self.ops.append("archive")
        self.state = "archived"

    def restore(self) -> None:
        self.ops.append("restore")
        self.state = "stopped"


class _FakePlatform:
    def __init__(self) -> None:
        self.sandboxes: dict[str, _FakeSandbox] = {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self._n = 0

    async def get(self, sandbox_id: str) -> _FakeSandbox | None:
        return self.sandboxes.get(sandbox_id)

    async def create(
        self,
        *,
        volume_id: str,
        mount_path: str,
        volume_subpath: str,
        labels: dict[str, str] | None = None,
        ephemeral: bool = False,
    ) -> _FakeSandbox:
        del ephemeral
        if not volume_subpath:
            raise ValueError("VolumeMount without workspace subpath is rejected")
        self._n += 1
        sid = f"sb-{self._n}"
        sb = _FakeSandbox(
            sid,
            state="running",
            volume_id=volume_id,
            mount_path=mount_path,
            volume_subpath=volume_subpath,
            labels=labels or {},
        )
        self.sandboxes[sid] = sb
        self.created.append(
            {
                "volume_id": volume_id,
                "mount_path": mount_path,
                "volume_subpath": volume_subpath,
                "labels": labels or {},
                "id": sid,
            }
        )
        return sb

    async def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)
        self.sandboxes.pop(sandbox_id, None)

    async def start(self, sandbox_id: str) -> None:
        self.sandboxes[sandbox_id].start()

    async def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None:
        del timeout, force
        self.sandboxes[sandbox_id].stop()


def _manager(
    platform: _FakePlatform | None = None,
    bindings: InMemoryBindingStore | None = None,
    admission: DaytonaAdmission | None = None,
    idle_stop_seconds: float | None = None,
) -> tuple[DaytonaSessionManager, _FakePlatform, InMemoryBindingStore, _FakeVolumeClient]:
    plat = platform or _FakePlatform()
    store = bindings or InMemoryBindingStore()
    volumes = _FakeVolumeClient()
    mgr = DaytonaSessionManager(
        platform=plat,
        volume_client=volumes,
        volume_config=VolumeConfig(),
        bindings=store,
        admission=admission,
        sandbox_spec=_SPEC,
        idle_stop_seconds=idle_stop_seconds,
    )
    return mgr, plat, store, volumes
