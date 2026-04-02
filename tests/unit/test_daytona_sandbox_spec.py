"""Tests for SandboxSpec declarative sandbox creation."""

from __future__ import annotations

from fleet_rlm.integrations.providers.daytona.types import SandboxSpec


class TestSandboxSpecDefaults:
    def test_default_values(self) -> None:
        spec = SandboxSpec()
        assert spec.language == "python"
        assert spec.volume_name is None
        assert spec.ephemeral is True
        assert spec.auto_stop_interval == 0
        assert spec.env_vars is None
        assert spec.labels is None
        assert spec.snapshot is None
        assert spec.image is None

    def test_to_create_params_minimal(self) -> None:
        spec = SandboxSpec()
        params = spec.to_create_params()
        assert params["language"] == "python"
        assert params["ephemeral"] is True
        assert params["auto_stop_interval"] == 0
        assert "volumes" not in params
        assert "env_vars" not in params

    def test_to_create_params_with_env_vars(self) -> None:
        spec = SandboxSpec(env_vars={"KEY": "value"})
        params = spec.to_create_params()
        assert params["env_vars"] == {"KEY": "value"}

    def test_to_create_params_with_labels(self) -> None:
        spec = SandboxSpec(labels={"managed-by": "fleet-rlm", "session": "abc"})
        params = spec.to_create_params()
        assert params["labels"]["managed-by"] == "fleet-rlm"
        assert params["labels"]["session"] == "abc"

    def test_to_create_params_with_volume(self) -> None:
        spec = SandboxSpec(
            volume_name="my-vol",
            volume_mount_path="/home/daytona/memory",
        )
        params = spec.to_create_params(volume_id="vol-123")
        assert len(params["volumes"]) == 1
        vol = params["volumes"][0]
        assert vol["volume_id"] == "vol-123"
        assert vol["mount_path"] == "/home/daytona/memory"
        assert "subpath" not in vol

    def test_to_create_params_with_volume_subpath(self) -> None:
        spec = SandboxSpec(
            volume_name="my-vol",
            volume_mount_path="/home/daytona/memory",
            volume_subpath="workspace-42",
        )
        params = spec.to_create_params(volume_id="vol-123")
        vol = params["volumes"][0]
        assert vol["subpath"] == "workspace-42"

    def test_to_create_params_no_volume_without_id(self) -> None:
        spec = SandboxSpec(
            volume_name="my-vol",
            volume_mount_path="/home/daytona/memory",
        )
        params = spec.to_create_params(volume_id=None)
        assert "volumes" not in params

    def test_to_create_params_with_snapshot(self) -> None:
        spec = SandboxSpec(snapshot="snap-abc")
        params = spec.to_create_params()
        assert params["snapshot"] == "snap-abc"

    def test_to_create_params_with_image(self) -> None:
        spec = SandboxSpec(image="python:3.12-slim")
        params = spec.to_create_params()
        assert "image" not in params  # image is handled at runtime level

    def test_env_vars_are_copied(self) -> None:
        original = {"A": "1"}
        spec = SandboxSpec(env_vars=original)
        params = spec.to_create_params()
        params["env_vars"]["B"] = "2"
        assert "B" not in original

    def test_labels_are_copied(self) -> None:
        original = {"x": "y"}
        spec = SandboxSpec(labels=original)
        params = spec.to_create_params()
        params["labels"]["z"] = "w"
        assert "z" not in original
