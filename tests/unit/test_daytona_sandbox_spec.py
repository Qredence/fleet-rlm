"""Tests for SandboxSpec declarative sandbox creation."""

from __future__ import annotations


from fleet_rlm.integrations.providers.daytona.types import SandboxSpec


class _FakeImage:
    """Lightweight stand-in for ``daytona.Image`` in unit tests."""

    def __init__(self, tag: str = "test") -> None:
        self.tag = tag


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
        assert spec.uses_declarative_image is False

    def test_to_create_params_minimal(self) -> None:
        spec = SandboxSpec()
        params = spec.to_create_params()
        assert params["language"] == "python"
        assert params["ephemeral"] is True
        assert params["auto_stop_interval"] == 0
        assert "volumes" not in params
        assert "env_vars" not in params
        assert "image" not in params

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

    def test_snapshot_ignored_when_image_set(self) -> None:
        """When both image and snapshot are set, image wins."""
        img = _FakeImage()
        spec = SandboxSpec(image=img, snapshot="snap-abc")
        params = spec.to_create_params()
        assert params["image"] is img
        assert "snapshot" not in params

    def test_to_create_params_with_declarative_image(self) -> None:
        img = _FakeImage("fleet-sandbox")
        spec = SandboxSpec(image=img)
        assert spec.uses_declarative_image is True
        params = spec.to_create_params()
        assert params["image"] is img

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

    def test_to_create_params_with_resources(self) -> None:
        spec = SandboxSpec(cpu=2, memory=4, disk=8)
        params = spec.to_create_params()
        assert params["resources"] == {"cpu": 2, "memory": 4, "disk": 8}

    def test_to_create_params_partial_resources(self) -> None:
        spec = SandboxSpec(cpu=2)
        params = spec.to_create_params()
        assert params["resources"] == {"cpu": 2}
        assert "memory" not in params["resources"]

    def test_to_create_params_no_resources_by_default(self) -> None:
        spec = SandboxSpec()
        params = spec.to_create_params()
        assert "resources" not in params

    def test_to_create_params_with_auto_delete_interval(self) -> None:
        spec = SandboxSpec(auto_delete_interval=60)
        params = spec.to_create_params()
        assert params["auto_delete_interval"] == 60

    def test_auto_delete_interval_absent_by_default(self) -> None:
        spec = SandboxSpec()
        params = spec.to_create_params()
        assert "auto_delete_interval" not in params


class TestSandboxSpecWithRealImage:
    """Tests using the actual ``daytona.Image`` declarative builder."""

    def test_declarative_image_in_spec(self) -> None:
        from daytona import Image

        img = (
            Image.debian_slim("3.12").pip_install(["requests"]).workdir("/home/daytona")
        )
        spec = SandboxSpec(image=img, labels={"env": "test"})
        assert spec.uses_declarative_image is True
        params = spec.to_create_params()
        assert params["image"] is img
        assert params["labels"]["env"] == "test"
        # The Image generates a real Dockerfile
        assert "requests" in img.dockerfile()

    def test_declarative_image_with_volume(self) -> None:
        from daytona import Image

        img = Image.debian_slim("3.12").env({"PROJECT": "fleet"})
        spec = SandboxSpec(
            image=img,
            volume_name="my-vol",
            volume_mount_path="/home/daytona/memory",
        )
        params = spec.to_create_params(volume_id="vol-abc")
        assert params["image"] is img
        assert params["volumes"][0]["volume_id"] == "vol-abc"
