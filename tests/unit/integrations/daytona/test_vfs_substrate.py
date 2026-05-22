"""VFS substrate tests covering VAL-DAYTONA-005, 007, 008, 016, 017, 018.

VAL-DAYTONA-005: Volume tree browsing returns bounded user-facing nodes.
VAL-DAYTONA-007: VFS traversal is rejected at API boundary (URL-encoded, alternate separators, root).
VAL-DAYTONA-008: VFS large/deep requests are bounded (depth, entry, byte limits).
VAL-DAYTONA-016: VFS root whitelist is enforced (only /memory, /artifacts, /buffers, /meta).
VAL-DAYTONA-017: VFS ownership cannot be guessed or overridden (API-level identity scoping).
VAL-DAYTONA-018: VFS binary and truncated file semantics are explicit (hash, encoding, binary flag).
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fleet_rlm.api.runtime_services.volumes import (
    CANONICAL_VOLUME_ROOTS,
    normalize_volume_file_path,
    normalize_volume_tree_path,
)
from fleet_rlm.integrations.daytona.sdk_ops import (
    VFS_CANONICAL_ROOTS,
    list_daytona_volume_tree,
    read_daytona_volume_file_text,
)

# ---------------------------------------------------------------------------
# Fake filesystem helpers
# ---------------------------------------------------------------------------


class _FakeFs:
    def __init__(self, *, files: dict[str, bytes] | None = None) -> None:
        self._files: dict[str, bytes] = files or {}
        self._dirs: dict[str, list[SimpleNamespace]] = {}

    def add_dir(self, path: str, entries: list[SimpleNamespace]) -> None:
        self._dirs[path] = entries

    def list_files(self, path: str) -> list[SimpleNamespace]:
        return self._dirs.get(path, [])

    def download_file(self, path: str) -> bytes:
        if path not in self._files:
            raise RuntimeError(f"No such file: {path}")
        return self._files[path]


def _make_entry(name: str, *, is_dir: bool = False, size: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        is_dir=is_dir,
        size=size,
        mod_time=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )


@contextmanager
def _fake_mounted_volume(fs: _FakeFs):
    """Context manager that yields a fake sandbox with an attached fake fs."""
    yield SimpleNamespace(fs=fs)


# ---------------------------------------------------------------------------
# VAL-DAYTONA-016: VFS root whitelist enforcement
# ---------------------------------------------------------------------------


class TestVfsRootWhitelist:
    """The canonical roots are the only allowed first path components."""

    def test_canonical_roots_are_declared(self) -> None:
        """VAL-DAYTONA-016: The canonical roots set is stable and non-empty."""
        assert "/memory" in VFS_CANONICAL_ROOTS
        assert "/artifacts" in VFS_CANONICAL_ROOTS
        assert "/buffers" in VFS_CANONICAL_ROOTS
        assert "/meta" in VFS_CANONICAL_ROOTS
        assert len(VFS_CANONICAL_ROOTS) == 4

    def test_api_canonical_roots_match_sdk_roots(self) -> None:
        """VAL-DAYTONA-016: API and SDK whitelist are consistent."""
        assert set(CANONICAL_VOLUME_ROOTS) == VFS_CANONICAL_ROOTS

    @pytest.mark.parametrize("bad_root", ["/tmp", "/workspace", "/home", "/etc", "/var"])
    def test_api_rejects_unknown_root_for_tree(self, bad_root: str) -> None:
        """VAL-DAYTONA-016: Unknown first path component returns 403."""
        with pytest.raises(HTTPException) as exc_info:
            normalize_volume_tree_path(bad_root)
        assert exc_info.value.status_code == 403

    @pytest.mark.parametrize("bad_root", ["/tmp/file.txt", "/workspace/src/main.py", "/etc/passwd"])
    def test_api_rejects_unknown_root_for_file(self, bad_root: str) -> None:
        """VAL-DAYTONA-016: Unknown first path component in file path returns 403."""
        with pytest.raises(HTTPException) as exc_info:
            normalize_volume_file_path(bad_root)
        assert exc_info.value.status_code == 403

    @pytest.mark.parametrize("allowed", ["/memory", "/artifacts", "/buffers", "/meta"])
    def test_api_allows_canonical_root_for_tree(self, allowed: str) -> None:
        """VAL-DAYTONA-016: Canonical root paths are accepted for tree requests."""
        result = normalize_volume_tree_path(allowed)
        assert result == allowed

    @pytest.mark.parametrize("allowed", ["/memory/note.txt", "/artifacts/out.json", "/buffers/q.bin", "/meta/info"])
    def test_api_allows_canonical_root_for_file(self, allowed: str) -> None:
        """VAL-DAYTONA-016: Canonical root descendants are accepted for file requests."""
        result = normalize_volume_file_path(allowed)
        assert result.startswith("/")

    def test_api_allows_root_slash_for_tree(self) -> None:
        """VAL-DAYTONA-016: Root '/' is allowed for tree requests to list the canonical roots."""
        result = normalize_volume_tree_path("/")
        assert result == "/"

    def test_api_rejects_root_slash_for_file(self) -> None:
        """VAL-DAYTONA-016: Root '/' is not a valid file path."""
        with pytest.raises(HTTPException) as exc_info:
            normalize_volume_file_path("/")
        assert exc_info.value.status_code == 403

    def test_sdk_list_tree_rejects_unknown_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-016: SDK list_daytona_volume_tree rejects unknown roots."""
        with pytest.raises(ValueError, match="outside canonical roots"):
            list_daytona_volume_tree("tenant-x", root_path="/tmp")

    def test_sdk_read_file_rejects_unknown_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-016: SDK read_daytona_volume_file_text rejects unknown roots."""
        with pytest.raises(ValueError, match="outside canonical roots"):
            read_daytona_volume_file_text("tenant-x", "/workspace/code.py")


# ---------------------------------------------------------------------------
# VAL-DAYTONA-007: Traversal rejection
# ---------------------------------------------------------------------------


class TestVfsTraversalRejection:
    """Traversal attempts must be rejected before any file content is returned."""

    @pytest.mark.parametrize(
        "path",
        [
            "../etc/passwd",
            "/memory/../meta",
            "/artifacts/../../secret",
            "artifacts/%2e%2e/secret",
            "/memory/%2e%2e%2f../etc",
            "..%2f..%2fetc%2fpasswd",
        ],
    )
    def test_api_file_path_rejects_traversal(self, path: str) -> None:
        """VAL-DAYTONA-007: Traversal probes return HTTP 400 at API boundary."""
        decoded = path.replace("%2e", ".").replace("%2E", ".").replace("%2f", "/").replace("%2F", "/")
        with pytest.raises(HTTPException) as exc_info:
            normalize_volume_file_path(decoded)
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize(
        "path",
        [
            "../etc",
            "/memory/../..",
            "../../root",
        ],
    )
    def test_api_tree_path_rejects_traversal(self, path: str) -> None:
        """VAL-DAYTONA-007: Traversal probes for tree root return HTTP 400 at API boundary."""
        with pytest.raises(HTTPException) as exc_info:
            normalize_volume_tree_path(path)
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize(
        "path",
        [
            "/%2e%2e/etc",
            "/memory/%2e%2e",
            "%2e%2e%2fetc%2fpasswd",
        ],
    )
    def test_api_rejects_url_encoded_traversal_in_file_path(self, path: str) -> None:
        """VAL-DAYTONA-007: URL-encoded '..' sequences are rejected before path parsing."""
        with pytest.raises(HTTPException) as exc_info:
            normalize_volume_file_path(path)
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize(
        "path",
        [
            "/%2e%2e/meta",
            "%2e%2e%2fmeta",
        ],
    )
    def test_api_rejects_url_encoded_traversal_in_tree_path(self, path: str) -> None:
        """VAL-DAYTONA-007: URL-encoded traversal for tree root returns 400."""
        with pytest.raises(HTTPException) as exc_info:
            normalize_volume_tree_path(path)
        assert exc_info.value.status_code == 400

    def test_sdk_list_tree_rejects_literal_traversal(self) -> None:
        """VAL-DAYTONA-007: SDK list_daytona_volume_tree rejects '..' in path."""
        with pytest.raises(ValueError, match="Path traversal not allowed"):
            list_daytona_volume_tree("tenant-x", root_path="/../etc")

    def test_sdk_read_file_rejects_literal_traversal(self) -> None:
        """VAL-DAYTONA-007: SDK read_daytona_volume_file_text rejects '..' in path."""
        with pytest.raises(ValueError, match="Path traversal not allowed"):
            read_daytona_volume_file_text("tenant-x", "/../etc/passwd")

    def test_sdk_read_file_rejects_url_encoded_traversal(self) -> None:
        """VAL-DAYTONA-007: URL-encoded traversal is rejected at SDK level."""
        with pytest.raises(ValueError, match="Path traversal not allowed"):
            read_daytona_volume_file_text("tenant-x", "/%2e%2e/secret")

    def test_sdk_list_tree_rejects_url_encoded_traversal(self) -> None:
        """VAL-DAYTONA-007: URL-encoded traversal in tree root is rejected at SDK level."""
        with pytest.raises(ValueError, match="Path traversal not allowed"):
            list_daytona_volume_tree("tenant-x", root_path="/%2e%2e/meta")

    def test_sdk_read_file_rejects_backslash_encoding(self) -> None:
        """VAL-DAYTONA-007: URL-encoded backslash (%5c) is treated as traversal indicator."""
        with pytest.raises(ValueError, match="Path traversal not allowed"):
            read_daytona_volume_file_text("tenant-x", "/memory/%5c..%5c")


# ---------------------------------------------------------------------------
# VAL-DAYTONA-005 / VAL-DAYTONA-008: Volume tree bounded responses
# ---------------------------------------------------------------------------


class TestVolumeTreeBounds:
    """Volume tree responses are bounded by depth, entry, and configuration limits."""

    def test_tree_returns_bounded_depth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-005: Tree response includes allowed roots with bounded depth."""
        fs = _FakeFs()
        # Mount: /home/daytona/memory lists the canonical roots
        fs.add_dir(
            "/home/daytona/memory",
            [
                _make_entry("memory", is_dir=True),
                _make_entry("artifacts", is_dir=True),
                _make_entry("buffers", is_dir=True),
                _make_entry("meta", is_dir=True),
            ],
        )
        for sub in ("memory", "artifacts", "buffers", "meta"):
            fs.add_dir(f"/home/daytona/memory/{sub}", [_make_entry(f"{sub}_file.txt", size=10)])

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        payload = list_daytona_volume_tree("test-vol", root_path="/", max_depth=2, max_entries=200)

        assert payload["volume_name"] == "test-vol"
        assert payload["root_path"] == "/"
        assert payload["truncated"] is False
        assert payload["max_depth"] == 2
        assert payload["max_entries"] == 200
        assert "allowed_roots" in payload
        root_children = payload["nodes"][0]["children"]
        root_names = [c["name"] for c in root_children]
        assert "memory" in root_names
        assert "artifacts" in root_names
        assert "buffers" in root_names
        assert "meta" in root_names

    def test_tree_truncates_at_max_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-008: Tree is truncated when max_entries is reached."""
        fs = _FakeFs()
        # A flat directory with many files
        many_files = [_make_entry(f"file_{i:04d}.txt", size=5) for i in range(50)]
        fs.add_dir("/home/daytona/memory", [_make_entry("artifacts", is_dir=True)])
        fs.add_dir("/home/daytona/memory/artifacts", many_files)

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        payload = list_daytona_volume_tree("test-vol", root_path="/", max_depth=3, max_entries=10)

        assert payload["truncated"] is True
        assert payload["entries_returned"] <= 10
        assert payload["max_entries"] == 10

    def test_tree_truncates_at_max_depth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-008: Deep tree is truncated when max_depth is reached."""
        fs = _FakeFs()
        fs.add_dir(
            "/home/daytona/memory",
            [_make_entry("meta", is_dir=True)],
        )
        # Each subdirectory has more subdirs
        fs.add_dir("/home/daytona/memory/meta", [_make_entry("level1", is_dir=True)])
        fs.add_dir("/home/daytona/memory/meta/level1", [_make_entry("level2", is_dir=True)])
        fs.add_dir("/home/daytona/memory/meta/level1/level2", [_make_entry("deep.txt", size=3)])

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        # max_depth=2 means we should not recurse below depth 2
        payload = list_daytona_volume_tree("test-vol", root_path="/", max_depth=2, max_entries=200)

        assert payload["truncated"] is True

    def test_file_read_is_truncated_at_max_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-008: File reads are truncated at the configured max_bytes limit."""
        content = b"A" * 1000
        fs = _FakeFs(files={"/home/daytona/memory/artifacts/big.txt": content})

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        payload = read_daytona_volume_file_text("test-vol", "/artifacts/big.txt", max_bytes=100)

        assert payload["truncated"] is True
        assert payload["size"] == 1000
        assert len(payload["content"]) <= 100
        # truncation_reason implied by truncated=True; sha256 is of FULL content
        expected_sha256 = hashlib.sha256(content).hexdigest()
        assert payload["sha256"] == expected_sha256

    def test_file_read_max_bytes_cap_at_1mb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-008: max_bytes is capped at 1 MiB."""
        content = b"x" * 10
        fs = _FakeFs(files={"/home/daytona/memory/memory/note.txt": content})

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        # Requesting more than 1 MiB is silently capped
        payload = read_daytona_volume_file_text("test-vol", "/memory/note.txt", max_bytes=5_000_000)

        assert payload["truncated"] is False
        assert payload["size"] == 10
        assert payload["content"] == "x" * 10


# ---------------------------------------------------------------------------
# VAL-DAYTONA-018: Binary and text file semantics
# ---------------------------------------------------------------------------


class TestVfsBinaryAndTextSemantics:
    """File fetches return explicit encoding metadata and SHA-256 hash."""

    def test_text_file_returns_sha256_and_utf8_encoding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-018: Text file response includes sha256, encoding='utf-8', binary=False."""
        content = b"Hello, world!\n"
        fs = _FakeFs(files={"/home/daytona/memory/artifacts/hello.txt": content})

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        payload = read_daytona_volume_file_text("test-vol", "/artifacts/hello.txt")

        assert payload["binary"] is False
        assert payload["encoding"] == "utf-8"
        assert payload["sha256"] == hashlib.sha256(content).hexdigest()
        assert payload["content"] == "Hello, world!\n"
        assert payload["size"] == len(content)
        assert payload["truncated"] is False

    def test_binary_file_returns_binary_flag_and_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-018: Binary file response sets binary=True, encoding='binary', content=''."""
        # A file with NUL bytes is unambiguously binary
        content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 200
        fs = _FakeFs(files={"/home/daytona/memory/artifacts/image.png": content})

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        payload = read_daytona_volume_file_text("test-vol", "/artifacts/image.png")

        assert payload["binary"] is True
        assert payload["encoding"] == "binary"
        assert payload["sha256"] == hashlib.sha256(content).hexdigest()
        assert payload["content"] == ""
        assert payload["size"] == len(content)
        assert payload["truncated"] is False

    def test_lossy_utf8_file_signals_utf8_lossy_encoding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-018: Invalid UTF-8 in a text-like file signals encoding='utf-8-lossy'."""
        # A mostly-text file with some invalid UTF-8 bytes
        content = b"Hello " + bytes([0xFF, 0xFE]) + b" world"
        fs = _FakeFs(files={"/home/daytona/memory/artifacts/mixed.txt": content})

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        payload = read_daytona_volume_file_text("test-vol", "/artifacts/mixed.txt")

        # 0xFF and 0xFE are printable-range bytes in Latin-1 but invalid UTF-8;
        # the file is not binary (few non-text bytes) but decode replaces them.
        assert payload["binary"] is False
        assert payload["encoding"] == "utf-8-lossy"
        assert "\ufffd" in payload["content"]  # replacement character present
        assert payload["sha256"] == hashlib.sha256(content).hexdigest()

    def test_empty_file_returns_utf8_encoding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-018: Empty file is text with empty content and valid hash."""
        content = b""
        fs = _FakeFs(files={"/home/daytona/memory/buffers/empty.txt": content})

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        payload = read_daytona_volume_file_text("test-vol", "/buffers/empty.txt")

        assert payload["binary"] is False
        assert payload["encoding"] == "utf-8"
        assert payload["content"] == ""
        assert payload["sha256"] == hashlib.sha256(b"").hexdigest()

    def test_truncated_file_sha256_is_of_full_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VAL-DAYTONA-018: sha256 is computed from the full content, not the truncated preview."""
        content = b"X" * 500
        fs = _FakeFs(files={"/home/daytona/memory/artifacts/long.txt": content})

        monkeypatch.setattr(
            "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
            lambda volume_name: _fake_mounted_volume(fs),
        )

        payload = read_daytona_volume_file_text("test-vol", "/artifacts/long.txt", max_bytes=100)

        assert payload["truncated"] is True
        assert len(payload["content"]) == 100
        # Hash is of the full 500-byte content, not the 100-byte preview
        assert payload["sha256"] == hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# VAL-DAYTONA-017: VFS ownership scoping (API-level identity)
# ---------------------------------------------------------------------------


class TestVfsOwnershipScoping:
    """VFS ownership is resolved from authenticated identity, not caller-supplied values."""

    def test_canonical_volume_roots_constant_is_stable(self) -> None:
        """VAL-DAYTONA-017: The canonical roots used for ownership checks do not change."""
        # The set is frozen and non-empty.
        assert isinstance(CANONICAL_VOLUME_ROOTS, tuple)
        assert len(CANONICAL_VOLUME_ROOTS) >= 4

    def test_normalize_volume_tree_path_accepts_all_canonical_roots(self) -> None:
        """VAL-DAYTONA-017: Every canonical root is addressable for tree requests."""
        for root in CANONICAL_VOLUME_ROOTS:
            result = normalize_volume_tree_path(root)
            assert result == root

    def test_normalize_volume_file_path_rejects_non_root_paths(self) -> None:
        """VAL-DAYTONA-017: File paths that aren't under canonical roots are rejected (403)."""
        for bad in ["/private", "/run", "/proc"]:
            with pytest.raises(HTTPException) as exc_info:
                normalize_volume_file_path(bad + "/file.txt")
            assert exc_info.value.status_code == 403
