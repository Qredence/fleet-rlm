from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.core.volume_ops import list_volume_tree, read_volume_file_text


def test_list_volume_tree_raises_when_listdir_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeVolume:
        def listdir(self, path: str, recursive: bool = False):
            _ = path, recursive
            raise RuntimeError("listdir boom")

    monkeypatch.setattr(
        "fleet_rlm.core.volume_ops.modal.Volume.from_name",
        lambda *args, **kwargs: _FakeVolume(),
    )

    with pytest.raises(RuntimeError, match="listdir boom"):
        list_volume_tree("test-volume")


def test_read_volume_file_text_stops_after_preview_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeVolume:
        def __init__(self) -> None:
            self.read_calls = 0

        def listdir(self, path: str, recursive: bool = False):
            _ = path, recursive
            return []

        def read_file(self, path: str):
            _ = path
            for chunk in (b"aaaaa", b"bbbbb", b"ccccc"):
                self.read_calls += 1
                yield chunk

    fake_volume = _FakeVolume()
    monkeypatch.setattr(
        "fleet_rlm.core.volume_ops.modal.Volume.from_name",
        lambda *args, **kwargs: fake_volume,
    )

    payload = read_volume_file_text("test-volume", "/docs/readme.txt", max_bytes=6)

    assert payload["content"] == "aaaaab"
    assert payload["truncated"] is True
    assert payload["size"] == 10
    assert fake_volume.read_calls == 2


def test_read_volume_file_text_uses_metadata_size_when_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeVolume:
        def listdir(self, path: str, recursive: bool = False):
            _ = recursive
            assert path == "/docs"
            return [SimpleNamespace(path="notes.md", size=128, type=1)]

        def read_file(self, path: str):
            _ = path
            yield b"abcdefghij"
            yield b"klmnopqrst"
            yield b"uvwxyz"

    monkeypatch.setattr(
        "fleet_rlm.core.volume_ops.modal.Volume.from_name",
        lambda *args, **kwargs: _FakeVolume(),
    )

    payload = read_volume_file_text("test-volume", "/docs/notes.md", max_bytes=12)

    assert payload["content"] == "abcdefghijkl"
    assert payload["truncated"] is True
    assert payload["size"] == 128
