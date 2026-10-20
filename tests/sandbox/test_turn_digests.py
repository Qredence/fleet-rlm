"""Per-Turn workspace digests: safety caps, the in-sandbox SHA-256 cache, and the progress-based stall watch."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.config import FleetSettings
from fleet_rlm.replay import MAX_REPLAY_FILE_BYTES
from fleet_rlm.sandbox import digests as digests_module
from fleet_rlm.sandbox.digests import (
    DIGEST_CACHE_PATH,
    DIGEST_MAX_FILE_BYTES,
    DIGEST_STALL_SECONDS,
    WorkspaceDigestFailedError,
    WorkspaceDigestStalledError,
    digests_code,
    run_watched_python,
    unhashed_digest_size,
)
from fleet_rlm.sandbox.manager import DaytonaSandboxManager

# --- the digest walk and its cache, executed locally with /workspace redirected ---------------------------------


def _localize(code: str, tmp_path: Path) -> str:
    return code.replace("'/workspace'", repr(str(tmp_path / "workspace"))).replace(
        repr(DIGEST_CACHE_PATH), repr(str(tmp_path / "cache" / "digests.json"))
    )


def _walk(tmp_path: Path, **options: Any) -> tuple[dict[str, str], dict[str, Any]]:
    options.setdefault("racy_seconds", 0)
    code = _localize(digests_code(**options), tmp_path) + "print(json.dumps([digests, digest_stats]))"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, {})
    digests, stats = json.loads(out.getvalue())
    return digests, stats


def _workspace(tmp_path: Path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "workspace"
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    root.mkdir(exist_ok=True)
    return root


def _cache(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "cache" / "digests.json").read_text())


def test_files_over_the_byte_cap_are_recorded_by_size_and_mtime(tmp_path) -> None:
    _workspace(tmp_path, {"files/small.txt": b"tiny", "files/big.bin": b"x" * 64})
    digests, stats = _walk(tmp_path, max_file_bytes=16)
    assert digests["files/small.txt"] == hashlib.sha256(b"tiny").hexdigest()
    mtime = os.stat(tmp_path / "workspace/files/big.bin").st_mtime_ns
    assert digests["files/big.bin"] == f"unhashed:size=64:mtime_ns={mtime}"
    assert stats == {"hashed": 1, "cached": 0, "unhashed": 1, "truncated": False}
    assert "files/big.bin" not in _cache(tmp_path)["entries"]  # a size-only value is never cached


def test_the_file_cap_lists_artifacts_first_then_sorted_paths(tmp_path) -> None:
    _workspace(tmp_path, {f"files/{name}.txt": name.encode() for name in ("e", "a", "d", "b", "c")})
    _workspace(tmp_path, {"artifacts/z.txt": b"z", "a-top.txt": b"t"})
    digests, stats = _walk(tmp_path, max_files=3)
    assert list(digests) == ["artifacts/z.txt", "a-top.txt", "files/a.txt"]
    assert stats["truncated"] is True


def test_unchanged_files_are_served_from_the_cache_on_the_next_walk(tmp_path) -> None:
    _workspace(tmp_path, {"files/a.txt": b"alpha", "artifacts/r.csv": b"1,2"})
    first, first_stats = _walk(tmp_path)
    second, second_stats = _walk(tmp_path)
    assert (
        first
        == second
        == {
            "artifacts/r.csv": hashlib.sha256(b"1,2").hexdigest(),
            "files/a.txt": hashlib.sha256(b"alpha").hexdigest(),
        }
    )
    assert (first_stats["hashed"], first_stats["cached"]) == (2, 0)
    assert (second_stats["hashed"], second_stats["cached"]) == (0, 2)


def test_a_rewrite_with_the_same_size_and_restored_mtime_is_rehashed(tmp_path) -> None:
    root = _workspace(tmp_path, {"files/a.txt": b"alpha"})
    _walk(tmp_path)
    before = os.stat(root / "files/a.txt")
    time.sleep(0.01)
    (root / "files/a.txt").write_bytes(b"omega")  # same size
    os.utime(root / "files/a.txt", ns=(before.st_atime_ns, before.st_mtime_ns))  # mtime put back; ctime moves
    digests, stats = _walk(tmp_path)
    assert digests["files/a.txt"] == hashlib.sha256(b"omega").hexdigest()
    assert (stats["hashed"], stats["cached"]) == (1, 0)


def test_a_replaced_file_with_a_new_inode_is_rehashed(tmp_path) -> None:
    root = _workspace(tmp_path, {"files/a.txt": b"alpha", "files/b.txt": b"bravo"})
    _walk(tmp_path)
    os.replace(root / "files/b.txt", root / "files/a.txt")
    digests, _ = _walk(tmp_path)
    assert digests == {"files/a.txt": hashlib.sha256(b"bravo").hexdigest()}
    assert set(_cache(tmp_path)["entries"]) == {"files/a.txt"}  # deleted paths are pruned


def test_recently_modified_files_are_hashed_but_not_cached(tmp_path) -> None:
    _workspace(tmp_path, {"files/a.txt": b"alpha"})
    digests, _ = _walk(tmp_path, racy_seconds=3600)
    assert digests["files/a.txt"] == hashlib.sha256(b"alpha").hexdigest()
    assert _cache(tmp_path)["entries"] == {}


def _forged_entry(root: Path) -> dict[str, Any]:
    info = os.stat(root / "files/a.txt")
    return {"files/a.txt": [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, "0" * 64]}


@pytest.mark.parametrize(
    "cache",
    [
        lambda _entries: "not json",
        lambda entries: json.dumps({"version": 99, "root": "/workspace", "entries": entries}),
        lambda entries: json.dumps({"version": 1, "root": "/elsewhere", "entries": entries}),
        lambda _entries: json.dumps({"version": 1, "root": "/workspace", "entries": "nope"}),
    ],
    ids=["not_json", "other_version", "other_root", "bad_entries"],
)
def test_an_unreadable_or_foreign_cache_is_ignored(tmp_path, cache) -> None:
    root = _workspace(tmp_path, {"files/a.txt": b"alpha"})
    (tmp_path / "cache").mkdir()
    # Entries that would match the file's stat identity, but with a wrong digest: they must not be used.
    content = cache(_forged_entry(root)).replace('"/workspace"', json.dumps(str(root)))
    (tmp_path / "cache" / "digests.json").write_text(content)
    digests, stats = _walk(tmp_path)
    assert digests["files/a.txt"] == hashlib.sha256(b"alpha").hexdigest() and stats["cached"] == 0


def test_a_matching_cache_entry_is_trusted_only_on_its_full_stat_identity(tmp_path) -> None:
    root = _workspace(tmp_path, {"files/a.txt": b"alpha"})
    (tmp_path / "cache").mkdir()
    entries = _forged_entry(root)
    (tmp_path / "cache" / "digests.json").write_text(json.dumps({"version": 1, "root": str(root), "entries": entries}))
    digests, stats = _walk(tmp_path)
    assert digests["files/a.txt"] == "0" * 64 and stats["cached"] == 1  # identity matched: the cache is used
    entries["files/a.txt"][2] -= 1  # any field off (here ctime) means a re-read
    (tmp_path / "cache" / "digests.json").write_text(json.dumps({"version": 1, "root": str(root), "entries": entries}))
    digests, stats = _walk(tmp_path)
    assert digests["files/a.txt"] == hashlib.sha256(b"alpha").hexdigest() and stats["hashed"] == 1


def test_the_cache_is_outside_the_workspace_and_never_listed(tmp_path) -> None:
    assert not DIGEST_CACHE_PATH.startswith("/workspace/")
    _workspace(tmp_path, {"files/a.txt": b"alpha"})
    # Even a cache configured inside the workspace is skipped by the walk.
    inside = str(tmp_path / "workspace" / ".fleet-cache" / "digests.json")
    code = digests_code(cache_path=inside, racy_seconds=0).replace("'/workspace'", repr(str(tmp_path / "workspace")))
    for _ in range(2):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code + "print(json.dumps(digests))", {})
        assert list(json.loads(out.getvalue())) == ["files/a.txt"]
    assert Path(inside).is_file()


def test_the_byte_cap_matches_the_replay_snapshot_limit() -> None:
    assert DIGEST_MAX_FILE_BYTES == MAX_REPLAY_FILE_BYTES


@pytest.mark.parametrize(
    "value", ["unhashed:size=x:mtime_ns=1", "unhashed:size=1", "0" * 64, "", "unhashed:size=1:mtime_ns=2:extra"]
)
def test_only_well_formed_capped_digests_carry_a_size(value: str) -> None:
    assert unhashed_digest_size(value) is None
    assert unhashed_digest_size("unhashed:size=7:mtime_ns=3") == 7


# --- the watched runner, against real local processes -------------------------------------------------------------


def _local_exec(calls: list[int]) -> Any:
    def exec_python(source: str, timeout: int) -> str:
        calls.append(timeout)
        done = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, timeout=timeout)
        if done.returncode != 0:
            raise RuntimeError(done.stderr[-500:])
        return done.stdout

    return exec_python


def _run(tmp_path: Path, code: str, calls: list[int] | None = None, **options: Any) -> str:
    options.setdefault("poll_seconds", 0.1)
    options.setdefault("launch_wait_seconds", 0.1)
    return run_watched_python(
        _local_exec([] if calls is None else calls), code, run_root=str(tmp_path / "runs"), **options
    )


def test_a_quick_run_returns_from_the_launch_exec(tmp_path) -> None:
    calls: list[int] = []
    assert _run(tmp_path, "print('hello')", calls, launch_wait_seconds=10) == "hello\n"
    assert len(calls) == 1 and all(0 < timeout <= 60 for timeout in calls)  # control execs are short and bounded


def test_a_long_run_that_keeps_progressing_is_never_cut_off(tmp_path) -> None:
    code = "import time\nfor i in range(25):\n time.sleep(0.1)\n _fleet_progress('work', i, force=True)\nprint('ok')"
    calls: list[int] = []
    started = time.monotonic()
    assert _run(tmp_path, code, calls, stall_seconds=1.0) == "ok\n"
    assert time.monotonic() - started >= 2.5 > 1.0  # ran well past the stall window
    assert len(calls) > 2


def test_a_run_without_progress_is_stopped_and_killed(tmp_path) -> None:
    code = "import os,time\nprint(os.getpid(),flush=True)\ntime.sleep(60)"
    started = time.monotonic()
    with pytest.raises(WorkspaceDigestStalledError, match="no progress for 1 s"):
        _run(tmp_path, code, stall_seconds=1.0)
    assert time.monotonic() - started < 15
    (failed,) = (tmp_path / "runs").glob("*.failed")
    pid = int((failed / "out").read_text().split()[0])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("stalled worker still running")


def test_a_crashing_run_fails_with_its_error(tmp_path) -> None:
    with pytest.raises(WorkspaceDigestFailedError, match="boom"):
        _run(tmp_path, "raise SystemExit('boom')")


def test_a_cancelled_turn_kills_the_run(tmp_path) -> None:
    def cancelled() -> None:
        raise RuntimeError("Turn cancelled")

    with pytest.raises(RuntimeError, match="Turn cancelled"):
        _run(tmp_path, "import time\ntime.sleep(60)", check=cancelled)
    assert list((tmp_path / "runs").glob("*.failed"))


def test_a_retried_poll_or_launch_sees_the_same_run(tmp_path) -> None:
    run_dir = str(tmp_path / "runs" / "fixed")
    worker = digests_module._worker_source("print('once')", run_dir)
    launch = digests_module._launch_code(worker, run_dir, 5.0)
    exec_python = _local_exec([])
    first = json.loads(exec_python(launch, 30))
    assert first == {"state": "done", "output": "once\n"}
    assert json.loads(exec_python(launch, 30)) == first  # no second worker, same answer
    assert json.loads(exec_python(digests_module._poll_code(run_dir), 30)) == first


# --- the manager: turn_inputs through the watched runner -------------------------------------------------------------


def _watched_locally(tmp_path: Path, seen: dict[str, Any]) -> Any:
    def run(self: DaytonaSandboxManager, code: str) -> str:
        seen["stall"] = self.digest_stall_seconds
        local = _localize(code, tmp_path).replace("'/learned'", repr(str(tmp_path / "learned")))
        return _run(tmp_path, local, stall_seconds=self.digest_stall_seconds)

    return run


def test_turn_inputs_digest_through_the_watched_runner_and_reuse_the_cache(monkeypatch, tmp_path, caplog) -> None:
    _workspace(tmp_path, {"files/a.txt": b"alpha", "artifacts/out.txt": b"result"})
    seen: dict[str, Any] = {}
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python_watched", _watched_locally(tmp_path, seen))
    manager = DaytonaSandboxManager("key", "org", "volume", "digests", digest_stall_seconds=45.0)
    # Files written just now are inside the racy window and would not be cached yet.
    monkeypatch.setattr("fleet_rlm.sandbox.manager.digests_code", lambda: digests_code(racy_seconds=0))
    first = manager.turn_inputs()
    second = manager.turn_inputs()
    assert seen["stall"] == 45.0
    assert (
        first["digests"]
        == second["digests"]
        == {
            "artifacts/out.txt": hashlib.sha256(b"result").hexdigest(),
            "files/a.txt": hashlib.sha256(b"alpha").hexdigest(),
        }
    )
    assert first["digest_stats"] == {"truncated": False, "unhashed": 0, "files": 2, "hashed": 2, "cached": 0}
    assert second["digest_stats"] == {"truncated": False, "unhashed": 0, "files": 2, "hashed": 0, "cached": 2}
    assert first["environment"].startswith("Sandbox environment: Python ")
    assert not any("capped" in record.getMessage() for record in caplog.records)


def test_turn_inputs_warn_and_tell_the_model_when_the_listing_is_truncated(monkeypatch, tmp_path, caplog) -> None:
    _workspace(tmp_path, {f"files/{index}.txt": b"x" for index in range(4)})
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python_watched", _watched_locally(tmp_path, {}))
    monkeypatch.setattr("fleet_rlm.sandbox.manager.digests_code", lambda: digests_code(max_files=2))
    inputs = DaytonaSandboxManager("key", "org", "volume", "digests").turn_inputs()
    assert inputs["digest_stats"]["truncated"] is True and inputs["digest_stats"]["files"] == 2
    assert "Workspace listing truncated: only the first 2 files (artifacts/ first" in inputs["environment"]
    assert any("was capped" in record.getMessage() for record in caplog.records)


def test_the_watched_exec_goes_through_short_bounded_process_execs(monkeypatch) -> None:
    timeouts: list[Any] = []

    def process_exec(command: str, timeout: Any = None) -> Any:
        timeouts.append(timeout)
        done = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return SimpleNamespace(exit_code=done.returncode, result=done.stdout)

    sandbox = SimpleNamespace(process=SimpleNamespace(exec=process_exec))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)
    manager = DaytonaSandboxManager("key", "org", "volume", "digests")
    assert manager.exec_remote_python_watched("print('ok')") == "ok\n"
    assert timeouts and all(timeout is not None and timeout <= 60 for timeout in timeouts)


def test_the_stall_window_is_configurable(monkeypatch) -> None:
    monkeypatch.delenv("FLEET_DAYTONA_DIGEST_STALL_SECONDS", raising=False)
    assert FleetSettings().daytona_digest_stall_seconds == DIGEST_STALL_SECONDS == 120.0
    monkeypatch.setenv("FLEET_DAYTONA_DIGEST_STALL_SECONDS", "300")
    assert FleetSettings().daytona_digest_stall_seconds == 300.0
    for bad in ("5", "7200"):
        monkeypatch.setenv("FLEET_DAYTONA_DIGEST_STALL_SECONDS", bad)
        with pytest.raises(ValueError):
            FleetSettings()
