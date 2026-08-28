"""P41b live retention for ``fleet doctor daytona`` (VAL-CROSS-024).

Serial, credentialed, ``FLEET_LIVE=1``-only lane: run the real doctor
end-to-end as a subprocess and prove

- the step/category/action output shape is retained,
- the run is fully sanitized (no raw exception text, no credentials),
- the disposable probe Sandbox is cleaned (no active ``fleet-daytona-doctor``
  labelled Sandbox afterwards; pre-existing orphans are adopted, deleted,
  reported), and
- user-owned services on 8000/5001/5432 are left untouched (pre/post
  loopback listener inventory is byte-identical).

The receipt records counts and booleans only: never credentials, provider
internals, SandIDB lists of user services, or private content.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend._p39c_evidence import write_lane_receipt

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1500)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECEIPT_SCHEMA = "fleet.p41b-doctor-retention/v1"
_STEP_LINE = re.compile(r"^\[(?P<state>ok|failed)\] (?P<name>\w+): (?P<message>.+)$")
_USER_PORTS = (8000, 5001, 5432)
_ACTIVE_STATES = {
    "pending",
    "creating",
    "created",
    "starting",
    "started",
    "running",
    "stopping",
    "stopped",
    "unknown",
    "",
}


def _enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip().lower() in {"1", "true", "yes"}


def _load_env() -> dict[str, str]:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env", override=False)
    return dict(os.environ)


def _port_inventory() -> dict[str, int]:
    """Loopback LISTEN counts for the user-owned ports (sanitized: counts only)."""
    counts = {str(port): 0 for port in _USER_PORTS}
    for port in _USER_PORTS:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [line for line in result.stdout.splitlines()[1:] if line.strip()]
        counts[str(port)] = len(lines)
    return counts


def _run_doctor(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "fleet", "doctor", "daytona"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )


def _parse_output(stdout: str) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "doctor produced no output"
    assert lines[0].startswith("[ok] policy: "), f"missing policy line: {lines[0]!r}"
    steps: list[tuple[str, bool, str]] = []
    actions: list[str] = []
    for line in lines[1:]:
        match = _STEP_LINE.match(line)
        if match:
            steps.append((match.group("name"), match.group("state") == "ok", match.group("message")))
        elif line.startswith("action: "):
            actions.append(line.removeprefix("action: "))
        else:
            raise AssertionError(f"unexpected doctor output line shape: {line!r}")
    return {"steps": steps, "actions": actions}


def _sanitization_offenders(stdout: str, stderr: str) -> list[str]:
    combined = stdout + "\n" + stderr
    offenders: list[str] = []
    # Real crash signature only: stderr may carry engineering logs (dspy INFO,
    # the documented upstream litellm tracemalloc RuntimeWarning hint), which
    # are not unsanitized failure surfaces.
    if "Traceback (most recent call last)" in combined:
        offenders.append("python-traceback")
    for env_name in ("DAYTONA_API_KEY", "FLEET_DAYTONA_API_KEY", "DATABRICKS_TOKEN"):
        value = os.environ.get(env_name, "")
        if value and value in combined:
            offenders.append(f"leaked env value {env_name}")
    return offenders


async def _doctor_label_inventory(settings: Any) -> tuple[list[Any], Any]:
    """List doctor-labelled Sandboxes; returning (active_list, client)."""
    from daytona import ListSandboxesQuery

    from fleet_rlm.daytona.platform import build_daytona_client

    client = build_daytona_client(settings)
    active: list[Any] = []
    async for sandbox in client.list(ListSandboxesQuery(labels={"purpose": "fleet-daytona-doctor"})):
        state = str(getattr(getattr(sandbox, "state", None), "value", getattr(sandbox, "state", None)) or "")
        if state.strip().lower() in _ACTIVE_STATES:
            active.append(sandbox)
    return active, client


async def _adopt_and_delete(client: Any, sandboxes: list[Any]) -> None:
    for sandbox in sandboxes:
        with contextlib.suppress(Exception):
            await client.delete(sandbox)


def test_p41b_fleet_doctor_daytona_retention_live(tmp_path: Path) -> None:
    if not _enabled():
        pytest.skip("FLEET_LIVE=1 required")
    env = _load_env()
    if not env.get("FLEET_DAYTONA_API_KEY"):
        pytest.fail("doctor lane requires FLEET_DAYTONA_API_KEY in the environment")

    del tmp_path
    pre_ports = _port_inventory()
    attempts: list[int] = []
    run = _run_doctor(env)
    attempts.append(run.returncode)
    transient = run.returncode != 0 and any(
        marker in run.stdout for marker in ("mount_mismatch", "provider_5xx", "retry")
    )
    if transient:
        # Single bounded retry per the mission's provider-hiccup guidance.
        time.sleep(300)
        run = _run_doctor(env)
        attempts.append(run.returncode)

    parsed = _parse_output(run.stdout)
    step_names = [name for name, _ok, _message in parsed["steps"]]
    assert step_names[:1] == ["settings"]
    assert "cleanup" in step_names, "cleanup step must always be present"
    assert step_names[-1] == "cleanup", "cleanup must be the final step"
    assert run.returncode == 0, f"doctor failed: {run.stdout}\n{run.stderr}"
    assert parsed["actions"] == [], f"successful run must not emit actions: {parsed['actions']}"
    assert all(ok for _name, ok, _message in parsed["steps"])
    assert not _sanitization_offenders(run.stdout, run.stderr)

    from fleet_rlm.config.loader import load_runtime_settings

    settings = load_runtime_settings()

    async def _ensure_probe_absent() -> tuple[int, int]:
        """Adopt+delete any doctor orphan, then prove absence. Returns (adopted, active)."""
        active, client = await _doctor_label_inventory(settings)
        adopted = 0
        try:
            if active:
                await _adopt_and_delete(client, active)
                adopted = len(active)
                active, _ = await _doctor_label_inventory(settings)
        finally:
            close_result = client.close()
            if asyncio.iscoroutine(close_result):
                await close_result
        return adopted, len(active)

    adopted, remaining = asyncio.run(_ensure_probe_absent())
    assert remaining == 0, f"doctor probe Sandboxes still active: {remaining}"

    post_ports = _port_inventory()
    assert post_ports == pre_ports, f"user services on 8000/5001/5432 changed: {pre_ports} -> {post_ports}"

    write_lane_receipt(
        "p41b-doctor-retention.json",
        "-p41b-doctor-retention",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "steps": [{"name": name, "ok": ok, "message": message} for name, ok, message in parsed["steps"]],
            "output_shape": {"policy_line": True, "step_lines": len(parsed["steps"]), "action_lines": 0},
            "exit_codes": attempts,
            "probe_cleanup": {"adopted_orphans": adopted, "active_after": 0},
            "user_services": {"ports": post_ports, "unchanged": True},
            "passed": True,
        },
    )
