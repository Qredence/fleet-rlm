from __future__ import annotations

import asyncio

from fleet_rlm.core.interpreter import ModalInterpreter


class _FakeReloadVolumes:
    """Mock Sandbox.reload_volumes callable with async support."""

    def __init__(self) -> None:
        self.sync_calls = 0
        self.async_calls = 0

    def __call__(self):
        self.sync_calls += 1

    async def aio(self):
        self.async_calls += 1


class _FakeSandbox:
    """Mock Modal Sandbox for testing."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.reload_volumes = _FakeReloadVolumes()

    def terminate(self):
        pass


class _FakeVolume:
    """Mock Modal Volume for testing."""

    def __init__(self, name, **kwargs):
        self.name = name
        self._kwargs = kwargs
        self._uploaded_dirs: list[tuple[str, str]] = []
        self._uploaded_files: list[tuple[str, str]] = []

    def commit(self):
        pass

    def reload(self):
        pass

    def batch_upload(self, *, force=False):
        self._kwargs["force"] = force
        return _FakeBatchUpload(self)


class _FakeBatchUpload:
    """Mock Modal batch upload context manager."""

    def __init__(self, volume: _FakeVolume):
        self._volume = volume

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def put_directory(self, local_path: str, remote_path: str):
        self._volume._uploaded_dirs.append((local_path, remote_path))

    def put_file(self, local_path: str, remote_path: str):
        self._volume._uploaded_files.append((local_path, remote_path))


class _FakeApp:
    """Mock Modal App for testing."""

    def __init__(self, name):
        self.name = name


class _FakeImage:
    """Mock Modal Image for testing."""

    def __init__(self, python_version=None):
        self.python_version = python_version


def test_modal_interpreter_volume_initialization(monkeypatch):
    """Test ModalInterpreter initializes with volume_name."""
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    image = _FakeImage(python_version="3.12")
    app = _FakeApp("test-app")

    interpreter = ModalInterpreter(
        image=image,
        app=app,
        timeout=600,
        idle_timeout=300,
        volume_name="test-volume",
    )

    assert interpreter.volume_name == "test-volume"
    assert interpreter.timeout == 600
    assert interpreter.idle_timeout == 300


def test_modal_interpreter_without_volume(monkeypatch):
    """Test ModalInterpreter works without volume_name."""
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    image = _FakeImage(python_version="3.12")
    app = _FakeApp("test-app")

    interpreter = ModalInterpreter(
        image=image,
        app=app,
        timeout=600,
    )

    assert interpreter.volume_name is None
    assert interpreter.timeout == 600
    assert interpreter.idle_timeout is None


def test_volume_commit_reload_methods(monkeypatch):
    """Test commit() and reload() methods exist and can be called."""
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    image = _FakeImage(python_version="3.12")
    app = _FakeApp("test-app")

    interpreter = ModalInterpreter(
        image=image,
        app=app,
        volume_name="test-volume",
    )

    # Should not raise when volume not mounted yet
    interpreter.commit()
    interpreter.reload()
    asyncio.run(interpreter.areload())


def test_volume_reload_uses_sandbox_refresh(monkeypatch):
    """reload() should refresh mounted volumes via the running sandbox."""
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    interpreter = ModalInterpreter(
        image=_FakeImage(python_version="3.12"),
        app=_FakeApp("test-app"),
        volume_name="test-volume",
    )
    interpreter._volume = _FakeVolume("test-volume")
    interpreter._sandbox = _FakeSandbox()

    interpreter.reload()

    assert interpreter._sandbox.reload_volumes.sync_calls == 1


def test_volume_areload_uses_async_sandbox_refresh(monkeypatch):
    """areload() should use Sandbox.reload_volumes.aio when available."""
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    interpreter = ModalInterpreter(
        image=_FakeImage(python_version="3.12"),
        app=_FakeApp("test-app"),
        volume_name="test-volume",
    )
    interpreter._volume = _FakeVolume("test-volume")
    interpreter._sandbox = _FakeSandbox()

    asyncio.run(interpreter.areload())

    assert interpreter._sandbox.reload_volumes.async_calls == 1


def test_volume_reload_noops_without_running_sandbox(monkeypatch):
    """reload helpers should be safe no-ops before the sandbox starts."""
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    interpreter = ModalInterpreter(
        image=_FakeImage(python_version="3.12"),
        app=_FakeApp("test-app"),
        volume_name="test-volume",
    )
    interpreter._volume = _FakeVolume("test-volume")

    interpreter.reload()
    asyncio.run(interpreter.areload())


def test_resolve_app_with_explicit_app(monkeypatch):
    """Test _resolve_app returns the explicit app when provided."""
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    app = _FakeApp("test-app")
    interpreter = ModalInterpreter(image=_FakeImage(), app=app)
    assert interpreter._resolve_app() is app


def test_resolve_app_deferred_lookup(monkeypatch):
    """Test _resolve_app calls App.lookup when no explicit app is provided."""
    lookup_calls: list[tuple] = []

    class _MockAppModule:
        """Mock the modal.App module-level class."""

        @staticmethod
        def lookup(name, *, create_if_missing=False):
            lookup_calls.append((name, create_if_missing))
            return _FakeApp(name)

    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _MockAppModule)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    interpreter = ModalInterpreter(image=_FakeImage(), app_name="my-app")
    # No lookup happens at __init__
    assert lookup_calls == []

    result = interpreter._resolve_app()
    assert len(lookup_calls) == 1
    assert lookup_calls[0] == ("my-app", True)
    assert result.name == "my-app"


def test_upload_to_volume(monkeypatch):
    """Test upload_to_volume uses batch_upload correctly."""
    created_volumes: list[_FakeVolume] = []

    def fake_volume_from_name(name, *, create_if_missing=False, version=None):
        vol = _FakeVolume(name, version=version)
        created_volumes.append(vol)
        return vol

    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)
    monkeypatch.setattr(
        "fleet_rlm.core.interpreter.modal.Volume.from_name",
        fake_volume_from_name,
    )

    interpreter = ModalInterpreter(
        image=_FakeImage(),
        app=_FakeApp("test"),
        volume_name="test-vol",
    )

    interpreter.upload_to_volume(
        local_dirs={"/local/knowledge": "/knowledge"},
        local_files={"/local/readme.md": "/readme.md"},
    )

    assert len(created_volumes) == 1
    vol = created_volumes[0]
    assert vol._kwargs.get("version") == 2
    assert vol._kwargs.get("force") is True
    assert vol._uploaded_dirs == [("/local/knowledge", "/knowledge")]
    assert vol._uploaded_files == [("/local/readme.md", "/readme.md")]


def test_upload_to_volume_no_volume_raises(monkeypatch):
    """Test upload_to_volume raises when no volume_name configured."""
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.App", _FakeApp)
    monkeypatch.setattr("fleet_rlm.core.interpreter.modal.Image", _FakeImage)

    interpreter = ModalInterpreter(image=_FakeImage(), app=_FakeApp("test"))
    try:
        interpreter.upload_to_volume(local_dirs={"/tmp": "/remote"})
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "No volume_name" in str(exc)
