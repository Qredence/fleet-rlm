"""Replay recording upgrades a capped (size and mtime) digest to the real SHA-256 of the bytes it stores."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.engine.turn import _prepare_inputs


def _runtime(digests: dict[str, str], files: dict[str, bytes]) -> Any:
    return SimpleNamespace(
        harness=SimpleNamespace(load_context=lambda _session: ("", ""), memories=lambda *_a, **_k: []),
        skill_mgr=SimpleNamespace(sync_local_skills_to_volume=lambda: None, build_catalog_xml=lambda: ""),
        sandbox_mgr=SimpleNamespace(
            turn_inputs=lambda: {"digests": dict(digests), "notes": [], "environment": ""},
            read_workspace_file=lambda path: files[path],
        ),
    )


def test_a_capped_digest_is_replaced_by_the_replayed_bytes_hash() -> None:
    files = {"files/a.txt": b"alpha", "files/b.txt": b"beta"}
    digests = {
        "files/a.txt": hashlib.sha256(b"alpha").hexdigest(),
        "files/b.txt": "unhashed:size=4:mtime_ns=123",
    }
    inputs = _prepare_inputs(_runtime(digests, files), "session-a", "task", record_replay=True)
    assert inputs["context_files"] == {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
    assert set(inputs["replay_files"]) == set(files)


def test_a_capped_digest_whose_size_changed_still_refuses_the_replay() -> None:
    files = {"files/b.txt": b"beta-but-longer"}
    with pytest.raises(ValueError, match="changed while the Turn was prepared"):
        _prepare_inputs(
            _runtime({"files/b.txt": "unhashed:size=4:mtime_ns=123"}, files), "session-a", "task", record_replay=True
        )


def test_without_replay_capped_digests_are_recorded_as_reported() -> None:
    digests = {"files/b.txt": "unhashed:size=4:mtime_ns=123"}
    inputs = _prepare_inputs(_runtime(digests, {}), "session-a", "task")
    assert inputs["context_files"] == digests and inputs["replay_files"] == {}
