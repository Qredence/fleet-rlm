"""Run-scoped sandboxed Chromium and agent-browser process cleanup (Linux)."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from uuid import UUID

_PROC = Path("/proc")
_PROCESS_NAMES = frozenset({"agent-browser", "chrome", "chromium", "chrome_crashpad_handler"})


def _run_id(scratch: str) -> str:
    path = Path(scratch)
    if not path.is_absolute() or path.parent != Path("/tmp/fleet") or path.is_symlink():
        raise ValueError("browser scratch must be /tmp/fleet/<Run UUID>")
    return str(UUID(path.name))


def _owned_processes(run_id: str) -> dict[int, str]:
    """Read identities from live processes, never trust a model-written PID file."""
    marker = f"FLEET_BROWSER_RUN={run_id}".encode()
    owned = {}
    for process in _PROC.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            environment = (process / "environ").read_bytes().split(b"\0")
            argv = (process / "cmdline").read_bytes().split(b"\0")
            if marker not in environment or not argv or Path(os.fsdecode(argv[0])).name not in _PROCESS_NAMES:
                continue
            stat = (process / "stat").read_text().rsplit(") ", 1)[1].split()
            if stat[0] != "Z":
                owned[int(process.name)] = stat[19]  # Linux process start time, protects against PID reuse.
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return owned


def _signal_owned(run_id: str, identities: dict[int, str], sig: signal.Signals) -> None:
    current = _owned_processes(run_id)
    for pid, started in identities.items():
        if current.get(pid) == started:
            with contextlib.suppress(ProcessLookupError):
                if os.getpgid(pid) == pid:
                    os.killpg(pid, sig)
                else:
                    os.kill(pid, sig)


def _groups_alive(groups: dict[int, str]) -> bool:
    # Sandboxed renderer processes can hide their environment from their
    # parent UID. Their process group must disappear too, not just its leader.
    for process in _PROC.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            stat = (process / "stat").read_text().rsplit(") ", 1)[1].split()
            if stat[0] != "Z" and int(stat[2]) in groups:
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


def close(scratch: str, *, groups: dict[int, str] | None = None) -> None:
    """Bounded cleanup of marked Chrome/driver processes; errors prevent reuse."""
    run_id = _run_id(scratch)
    owned = _owned_processes(run_id)
    groups = dict(groups or {})
    if not owned and not _groups_alive(groups):
        return
    for pid, started in owned.items():
        with contextlib.suppress(ProcessLookupError):
            if os.getpgid(pid) == pid:
                groups[pid] = started
    # Attached CDP Chrome is externally owned: closing the driver alone only
    # disconnects it. Terminate both browser and daemon, including crash helpers.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        _signal_owned(run_id, owned, sig)
        if sig == signal.SIGKILL:
            for group, started in groups.items():
                leader = _PROC / str(group) / "stat"
                # A remaining old group may have lost its leader. Refuse to
                # signal a newly reused leader identity.
                try:
                    if leader.read_text().rsplit(") ", 1)[1].split()[19] != started:
                        continue
                except FileNotFoundError:
                    pass
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(group, sig)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            owned = _owned_processes(run_id)
            if not owned and not _groups_alive(groups):
                return
            time.sleep(0.05)
    if _owned_processes(run_id) or _groups_alive(groups):
        raise RuntimeError("Run browser cleanup remains unresolved")


def start(scratch: str) -> dict[str, object]:
    """Launch fresh Chrome with its sandbox enabled; return a direct CLI prefix."""
    run_id = _run_id(scratch)
    if not _PROC.is_dir() or os.getuid() == 0:
        raise RuntimeError("browser launch requires non-root Linux")
    profile = Path(scratch) / "browser" / "profile"
    port_file = profile / "DevToolsActivePort"
    session = f"fleet-{run_id}"
    environment = {**os.environ, "FLEET_BROWSER_RUN": run_id, "AGENT_BROWSER_SESSION": session}
    executable = environment.get("AGENT_BROWSER_EXECUTABLE_PATH")
    if not executable or not Path(executable).is_file():
        raise RuntimeError("browser-enabled Fleet snapshot is required")
    if _owned_processes(run_id):
        raise RuntimeError("a browser is already open for this Run; reuse its CLI prefix")
    # A previous stopped browser's profile is never recovered implicitly.
    if profile.exists():
        raise RuntimeError("Run browser profile already exists")
    profile.mkdir(parents=True, mode=0o700)
    with (profile.parent / "browser.log").open("wb") as log:
        browser = subprocess.Popen(
            [
                executable,
                "--headless=new",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "--disable-background-networking",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            env=environment,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    groups = {}
    try:
        groups = {browser.pid: (_PROC / str(browser.pid) / "stat").read_text().rsplit(") ", 1)[1].split()[19]}
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if browser.poll() is not None:
                raise RuntimeError("sandboxed Chromium did not start")
            if port_file.is_file():
                lines = port_file.read_text().splitlines()
                if lines and lines[0].isdecimal() and 1 <= int(lines[0]) <= 65535:
                    endpoint = f"http://127.0.0.1:{lines[0]}"
                    # The driver inherits the marker so native cleanup can find
                    # its detached daemon as well as all Chrome descendants.
                    os.environ.update({"FLEET_BROWSER_RUN": run_id, "AGENT_BROWSER_SESSION": session})
                    return {
                        "command": ["agent-browser", "--session", session, "--cdp", endpoint],
                        "session": session,
                        "cdp_url": endpoint,
                        "process_groups": {str(pid): started for pid, started in groups.items()},
                    }
            time.sleep(0.05)
        raise TimeoutError("sandboxed Chromium startup timed out")
    except BaseException:
        close(scratch, groups=groups)
        browser.wait(timeout=5)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "close"))
    parser.add_argument("scratch")
    parser.add_argument("--groups", default="{}")
    args = parser.parse_args()
    if args.action == "start":
        print(json.dumps(start(args.scratch)))  # noqa: T201 - trusted launcher CLI protocol
    else:
        close(args.scratch, groups={int(pid): str(started) for pid, started in json.loads(args.groups).items()})
        print("fleet-browser-cleanup-ok")  # noqa: T201 - trusted cleanup CLI protocol
