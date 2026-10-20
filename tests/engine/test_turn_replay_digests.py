"""Capped (size and mtime) digests: recorded as reported, and a replay of such a file hits the snapshot limit."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.engine.turn import _prepare_inputs
from fleet_rlm.sandbox.digests import DIGEST_MAX_FILE_BYTES


def _runtime(digests: dict[str, str], files: dict[str, bytes]) -> Any:
    return SimpleNamespace(
        harness=SimpleNamespace(load_context=lambda _session: ("", ""), memories=lambda *_a, **_k: []),
        skill_mgr=SimpleNamespace(sync_local_skills_to_volume=lambda: None, build_catalog_xml=lambda: ""),
        sandbox_mgr=SimpleNamespace(
            turn_inputs=lambda: {"digests": dict(digests), "notes": [], "environment": ""},
            read_workspace_file=lambda path: files[path],
        ),
    )


def test_a_replay_of_a_capped_file_is_refused_by_the_snapshot_limit() -> None:
    big = b"x" * (DIGEST_MAX_FILE_BYTES + 1)  # the only size the digest walk records as unhashed
    files = {"files/a.txt": b"alpha", "files/big.bin": big}
    digests = {
        "files/a.txt": hashlib.sha256(b"alpha").hexdigest(),
        "files/big.bin": f"unhashed:size={len(big)}:mtime_ns=123",
    }
    with pytest.raises(ValueError, match="exceeds the 8 MB snapshot limit"):
        _prepare_inputs(_runtime(digests, files), "session-a", "task", record_replay=True)


def test_a_replay_refuses_a_file_that_changed_after_its_digest() -> None:
    files = {"files/a.txt": b"alpha-but-changed"}
    with pytest.raises(ValueError, match="changed while the Turn was prepared"):
        _prepare_inputs(
            _runtime({"files/a.txt": hashlib.sha256(b"alpha").hexdigest()}, files),
            "session-a",
            "task",
            record_replay=True,
        )


def test_without_replay_capped_digests_are_recorded_as_reported() -> None:
    digests = {"files/b.txt": "unhashed:size=4:mtime_ns=123"}
    inputs = _prepare_inputs(_runtime(digests, {}), "session-a", "task")
    assert inputs["context_files"] == digests and inputs["replay_files"] == {}
