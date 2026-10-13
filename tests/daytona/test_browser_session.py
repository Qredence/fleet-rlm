"""Browser process ownership and launch policy without real Chrome."""

import importlib.util
import os
import signal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

RESOURCE = Path("src/fleet_rlm/daytona/browser_session.py")


@pytest.fixture
def owner():
    spec = importlib.util.spec_from_file_location("browser_session", RESOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def process(proc, pid, *, run_id, name="chrome", started="123", state="S", group=0):
    path = proc / str(pid)
    path.mkdir()
    (path / "environ").write_bytes(f"FLEET_BROWSER_RUN={run_id}\0".encode())
    (path / "cmdline").write_bytes(f"/opt/fleet/{name}\0--headless\0".encode())
    fields = [state] + ["0"] * 18 + [started]
    fields[2] = str(group)
    (path / "stat").write_text(f"{pid} (browser name) " + " ".join(fields))
    return path


def test_cleanup_only_targets_live_marked_browser_processes_and_guards_pid_reuse(owner, monkeypatch, tmp_path):
    run_id = str(uuid4())
    proc = tmp_path / "proc"
    proc.mkdir()
    process(proc, 100, run_id=run_id)
    process(proc, 101, run_id=str(uuid4()))
    process(proc, 102, run_id=run_id, name="python")
    process(proc, 103, run_id=run_id, name="agent-browser", state="Z")
    monkeypatch.setattr(owner, "_PROC", proc)
    monkeypatch.setattr(owner.os, "getpgid", lambda _pid: 1000)
    killed = []
    monkeypatch.setattr(owner.os, "kill", lambda *args: killed.append(args))
    assert owner._owned_processes(run_id) == {100: "123"}
    owner._signal_owned(run_id, {100: "old-start-time"}, signal.SIGTERM)
    assert killed == []
    owner._signal_owned(run_id, {100: "123", 101: "123"}, signal.SIGTERM)
    assert killed == [(100, signal.SIGTERM)]


def test_unused_session_cleanup_does_not_start_a_driver(owner, monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "_PROC", tmp_path)
    monkeypatch.setattr(owner.subprocess, "Popen", lambda *_a, **_k: pytest.fail("cleanup launched a process"))
    owner.close(f"/tmp/fleet/{uuid4()}")


def test_cleanup_escalates_and_refuses_unconfirmed_processes(owner, monkeypatch):
    signals = []
    monkeypatch.setattr(owner, "_owned_processes", lambda _run: {100: "123"})
    monkeypatch.setattr(owner, "_signal_owned", lambda _run, _ids, sig: signals.append(sig))
    monkeypatch.setattr(owner.os, "getpgid", lambda _pid: 1000)
    monkeypatch.setattr(owner, "_groups_alive", lambda _groups: False)
    ticks = iter(range(100))
    monkeypatch.setattr(owner.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(owner.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="unresolved"):
        owner.close(f"/tmp/fleet/{uuid4()}")
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_launch_keeps_chrome_sandbox_and_returns_only_run_scoped_cdp_prefix(owner, monkeypatch, tmp_path):
    run_id = str(uuid4())
    monkeypatch.setattr(owner, "_run_id", lambda _scratch: run_id)
    monkeypatch.setattr(owner, "_PROC", tmp_path)
    monkeypatch.setattr(owner.os, "getuid", lambda: 1000)
    monkeypatch.setattr(owner, "_owned_processes", lambda _run: {})
    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("AGENT_BROWSER_EXECUTABLE_PATH", str(executable))
    started = []

    def launch(command, **kwargs):
        started.append((command, kwargs))
        (tmp_path / "browser/profile/DevToolsActivePort").write_text("9222\n/devtools/browser/fixture\n")
        process(tmp_path, 100, run_id=run_id, group=100)
        return SimpleNamespace(pid=100, poll=lambda: None)

    monkeypatch.setattr(owner.subprocess, "Popen", launch)
    # start intentionally exports the marker for following direct CLI calls.
    monkeypatch.setenv("FLEET_BROWSER_RUN", "before")
    monkeypatch.setenv("AGENT_BROWSER_SESSION", "before")
    result = owner.start(str(tmp_path))
    assert result["command"] == ["agent-browser", "--session", f"fleet-{run_id}", "--cdp", "http://127.0.0.1:9222"]
    command, options = started[0]
    assert "--no-sandbox" not in command
    assert "--remote-debugging-address=127.0.0.1" in command
    assert options["start_new_session"] is True
    assert options["env"]["FLEET_BROWSER_RUN"] == run_id
    assert result["process_groups"] == {"100": "123"}
    assert os.environ["FLEET_BROWSER_RUN"] == run_id


def test_cleanup_kills_orphan_group_using_host_retained_launch_identity(owner, monkeypatch, tmp_path):
    run_id = str(uuid4())
    # The original group leader is gone; its renderer hides the Run environment.
    renderer = process(tmp_path, 101, run_id="hidden", group=100)
    monkeypatch.setattr(owner, "_PROC", tmp_path)
    killed = []

    def kill_group(group, sig):
        killed.append((group, sig))
        (renderer / "stat").unlink()

    monkeypatch.setattr(owner.os, "killpg", kill_group)
    ticks = iter(range(100))
    monkeypatch.setattr(owner.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(owner.time, "sleep", lambda _seconds: None)
    owner.close(f"/tmp/fleet/{run_id}", groups={100: "123"})
    assert killed == [(100, signal.SIGKILL)]
    assert not owner._groups_alive({100: "123"})


def test_cleanup_refuses_to_signal_reused_group_leader(owner, monkeypatch, tmp_path):
    process(tmp_path, 100, run_id="unrelated", group=100, started="new")
    monkeypatch.setattr(owner, "_PROC", tmp_path)
    monkeypatch.setattr(owner.os, "killpg", lambda *_args: pytest.fail("signaled a reused leader"))
    ticks = iter(range(100))
    monkeypatch.setattr(owner.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(owner.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="unresolved"):
        owner.close(f"/tmp/fleet/{uuid4()}", groups={100: "123"})


@pytest.mark.parametrize("scratch", ["/workspace", "/tmp/fleet/not-a-uuid", "relative", "/tmp/fleet/../workspace"])
def test_browser_owner_rejects_unscoped_scratch(owner, scratch):
    with pytest.raises(ValueError):
        owner.close(scratch)
