"""Declarative sandbox specification and builder helpers for Daytona."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class SandboxSpec:
    """Declarative specification for Daytona sandbox creation.

    Wraps the Daytona SDK's ``Image`` declarative builder and
    ``CreateSandboxFrom*Params`` into a single portable object.
    """

    name: str | None = None
    language: str = "python"
    image: Any = None
    snapshot: str | None = None
    volume_name: str | None = None
    volume_mount_path: str | None = None
    volume_subpath: str | None = None
    env_vars: dict[str, str] | None = None
    labels: dict[str, str] | None = None
    ephemeral: bool = True
    auto_stop_interval: int | None = 30
    auto_archive_interval: int | None = 60
    auto_delete_interval: int | None = None
    cpu: int | None = None
    memory: int | None = None
    disk: int | None = None
    network_block_all: bool | None = None
    network_allow_list: str | None = None

    @property
    def uses_declarative_image(self) -> bool:
        """True when the spec carries a ``daytona.Image`` declarative builder."""
        return self.image is not None

    def _common_params(self, *, volume_id: str | None = None) -> dict[str, Any]:
        """Build shared keyword arguments for any SDK create-params constructor."""
        params: dict[str, Any] = {"language": self.language}
        if self.name:
            params["name"] = self.name
        if self.env_vars:
            params["env_vars"] = dict(self.env_vars)
        if self.labels:
            params["labels"] = dict(self.labels)
        if self.ephemeral is not None:
            params["ephemeral"] = self.ephemeral
        params.update(self._daytona_lifecycle_params())
        if self.snapshot and not self.image:
            params["snapshot"] = self.snapshot
        if self.cpu is not None or self.memory is not None or self.disk is not None:
            params["resources"] = {
                key: value
                for key, value in [
                    ("cpu", self.cpu),
                    ("memory", self.memory),
                    ("disk", self.disk),
                ]
                if value is not None
            }
        if self.network_block_all is not None:
            params["network_block_all"] = self.network_block_all
        if self.network_allow_list is not None:
            params["network_allow_list"] = self.network_allow_list
        if volume_id and self.volume_mount_path:
            mount_kwargs: dict[str, Any] = {
                "volume_id": volume_id,
                "mount_path": self.volume_mount_path,
            }
            if self.volume_subpath:
                mount_kwargs["subpath"] = self.volume_subpath
            params["volumes"] = [mount_kwargs]
        return params

    def _daytona_lifecycle_params(self) -> dict[str, int]:
        """Return Daytona lifecycle settings in provider-minute units."""
        params: dict[str, int] = {}
        if self.auto_stop_interval is not None:
            params["auto_stop_interval"] = self.auto_stop_interval
        if self.auto_archive_interval is not None:
            params["auto_archive_interval"] = self.auto_archive_interval
        if self.auto_delete_interval is not None:
            params["auto_delete_interval"] = self.auto_delete_interval
        return params

    def to_create_params(self, *, volume_id: str | None = None) -> dict[str, Any]:
        """Build keyword arguments for the SDK create-params constructor."""
        params = self._common_params(volume_id=volume_id)
        if self.image is not None:
            params["image"] = self.image
        return params

    def to_daytona_create_params(
        self,
        *,
        volume_id: str | None = None,
        create_image_params_cls: Any,
        create_snapshot_params_cls: Any,
        volume_mount_cls: Any,
        resources_cls: Any,
    ) -> Any:
        """Build the concrete Daytona SDK create-params object for this spec."""
        params = self._common_params(volume_id=None)
        if volume_id and self.volume_mount_path:
            mount_kwargs: dict[str, Any] = {
                "volume_id": volume_id,
                "mount_path": self.volume_mount_path,
            }
            if self.volume_subpath:
                mount_kwargs["subpath"] = self.volume_subpath
            params["volumes"] = [volume_mount_cls(**mount_kwargs)]
        resources = params.pop("resources", None)
        if resources and self.uses_declarative_image:
            params["resources"] = resources_cls(**resources)
        if self.uses_declarative_image:
            params["image"] = self.image
            return create_image_params_cls(**params)
        return create_snapshot_params_cls(**params)


DEFAULT_SANDBOX_LABELS: dict[str, str] = {"managed-by": "fleet-rlm"}


def default_sandbox_name(*, now: datetime.datetime | None = None) -> str:
    """Return the dashboard-friendly default sandbox name."""
    timestamp = now or datetime.datetime.now(datetime.timezone.utc)
    return f"fleet-rlm-{timestamp:%Y%m%d-%H%M%S}"


def merge_sandbox_labels(
    *,
    default_labels: Mapping[str, str] | None = None,
    labels: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge runtime-default labels with caller-provided labels."""
    merged = dict(default_labels or DEFAULT_SANDBOX_LABELS)
    if labels:
        merged.update(labels)
    return merged


def build_sandbox_spec(
    *,
    default_labels: Mapping[str, str] | None = None,
    name: str | None = None,
    volume_name: str | None = None,
    volume_subpath: str | None = None,
    image: Any = None,
    snapshot: str | None = None,
    env_vars: Mapping[str, str] | None = None,
    labels: Mapping[str, str] | None = None,
    cpu: int | None = None,
    memory: int | None = None,
    disk: int | None = None,
    auto_stop_interval: int | None = 30,
    auto_archive_interval: int | None = 60,
    auto_delete_interval: int | None = None,
    network_block_all: bool | None = None,
    network_allow_list: str | None = None,
) -> SandboxSpec:
    """Build a ``SandboxSpec`` with Daytona runtime defaults applied."""
    from .snapshot_runtime import resolve_default_snapshot
    from .volume_runtime import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH

    return SandboxSpec(
        name=name or default_sandbox_name(),
        language="python",
        image=image,
        snapshot=resolve_default_snapshot(image=image, snapshot=snapshot),
        volume_name=volume_name,
        volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
        volume_subpath=volume_subpath,
        env_vars=dict(env_vars) if env_vars else None,
        labels=merge_sandbox_labels(default_labels=default_labels, labels=labels),
        ephemeral=True,
        auto_stop_interval=auto_stop_interval,
        auto_archive_interval=auto_archive_interval,
        auto_delete_interval=auto_delete_interval,
        cpu=cpu,
        memory=memory,
        disk=disk,
        network_block_all=network_block_all,
        network_allow_list=network_allow_list,
    )


__all__ = [
    "DEFAULT_SANDBOX_LABELS",
    "SandboxSpec",
    "build_sandbox_spec",
    "default_sandbox_name",
    "merge_sandbox_labels",
]
