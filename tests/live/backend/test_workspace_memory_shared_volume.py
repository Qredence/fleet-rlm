"""QRE-152 live evidence: cross-Sandbox Workspace Memory mutation on the shared Volume.

Gate: FLEET_LIVE=1 plus the daytona profile credentials (repo ``.env``,
override=False). Independent ephemeral Sandboxes A/B/C mount the SAME Workspace
Volume Scope; Memory mutations run through the production shipped agent
(``run_workspace_agent_async``) so the stable-lock / inode-revalidation / fsync
protocol is characterized under real provider semantics.

LIVE-VERIFIED VERDICT (2026-08-17, v5 snapshot):

- ``fcntl.flock`` is NOT coordinated across independently mounted Sandboxes on
  the real Daytona shared Volume (contender acquires while a peer holds
  ``LOCK_EX``) — the in-sandbox lock protocol cannot exclude a foreign mount.
- Concurrent cross-Sandbox ``memory_append`` therefore loses records SILENTLY:
  appends return ``ok`` while their record never reaches the final file
  (fail-closed conflict errors cover only part of the races).
- What DOES hold on the real provider: sequential cross-Sandbox appends remain
  correct; mixed-op races keep the file parseable with single record
  occurrences (losers fail closed); writer death mid-lock cannot wedge the
  Memory lock (process death releases fds); a completed mutation is visible
  from a third Sandbox; every write records its ladder fallback explicitly
  (``non_atomic_overwrite``).

DESIGN DECISION REQUIRED BY THE VERDICT: cross-Sandbox Workspace Memory
serialization must move above the Volume. The follow-up strategy (recorded on
QRE-152): a Fleet-side per-Workspace mutation lease in the authoritative SQL
store (lease row + fencing token, fail-closed) wrapping every Memory mutation
(Tool write, candidate promotion, REST edit), with the in-process per-Workspace
async lock retained as the fast path and the in-sandbox flock retained as an
in-kernel guard only. Until that lands, the coordination invariant stays alive
here as a marked expectation: ``test_live_shared_volume_cross_sandbox_append_coordination``
is ``xfail(strict=False)`` — an XPASS is the signal that the lease strategy
works and the mark must be removed, never weakened.

Receipt: ``fleet.qre152-memory-concurrency/v1`` written next to
``FLEET_LIVE_EVIDENCE_PATH`` (suffix ``-memory-shared-volume``) when set.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1800)]

_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_RECEIPT_SCHEMA = "fleet.qre152-memory-concurrency/v1"
_AGENT_TIMEOUT_S = 120.0
_MAX_MEMORY_BYTES = 1_048_576


def _load_repo_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)


def _write_receipt(payload: dict[str, Any], *, suffix: str = "memory-shared-volume") -> None:
    raw = os.environ.get(_EVIDENCE_ENV)
    if not raw:
        return
    base = Path(raw)
    target = base.with_name(f"{base.stem}-{suffix}{base.suffix or '.json'}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record(learning: str, memory_id: str, *, supersedes_id: str | None = None) -> str:
    from fleet_rlm.workspace.models import format_workspace_memory_v3_record

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return format_workspace_memory_v3_record(
        learning,
        "probe",
        memory_id=memory_id,
        created_at=now,
        updated_at=now,
        source="operator_import",
        supersedes_id=supersedes_id,
    )


def _b64(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("ascii")


@dataclass
class _Probe:
    mount_path: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    async def op(self, sandbox: Any, **kwargs: Any) -> dict[str, Any]:
        from fleet_rlm.daytona.workspace_agent.client import run_workspace_agent_async

        args: dict[str, Any] = {
            "volume_root": self.mount_path,
            "root": f"{self.mount_path}/memory",
            "operation": "read",
            "relative": "MEMORIES.md",
            "allow_missing": True,
            "max_bytes": _MAX_MEMORY_BYTES,
            "limit": 0,
            "overwrite": False,
            "content_b64": "",
            "after": "",
            "offset": 0,
            "max_chars": 0,
            "total_file_bytes": _MAX_MEMORY_BYTES,
            "checksum": False,
            "memory_id": "",
            "expected_sha256": "",
        }
        args.update(kwargs)
        label = f"{args['operation']}:{args.get('memory_id') or ''}"
        started = asyncio.get_running_loop().time()
        try:
            payload = await run_workspace_agent_async(sandbox, timeout_s=_AGENT_TIMEOUT_S, **args)
        except Exception as exc:  # classified, never silent
            outcome = {
                "op": label,
                "ok": False,
                "elapsed_s": round(asyncio.get_running_loop().time() - started, 3),
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
            self.observations.append(outcome)
            return outcome
        warn = payload.get("warnings") or []
        for w in warn:
            self.warnings.append({"op": label, **(w if isinstance(w, dict) else {"code": str(w)})})
        outcome = {
            "op": label,
            "ok": True,
            "elapsed_s": round(asyncio.get_running_loop().time() - started, 3),
            "warnings": [str(w.get("code")) for w in warn if isinstance(w, dict)],
        }
        self.observations.append(outcome)
        return {"ok": True, "payload": payload, "elapsed_s": outcome["elapsed_s"]}

    async def read_memory(self, sandbox: Any) -> str:
        result = await self.op(sandbox, operation="read", allow_missing=True)
        if not result.get("ok"):
            return ""
        content = result["payload"].get("content")
        return content if isinstance(content, str) else ""

    async def append(self, sandbox: Any, record: str) -> dict[str, Any]:
        return await self.op(sandbox, operation="memory_append", content_b64=_b64(record))

    async def edit(self, sandbox: Any, memory_id: str, learning: str) -> dict[str, Any]:
        request = json.dumps(
            {
                "learning": learning,
                "category": None,
                "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            separators=(",", ":"),
        )
        return await self.op(
            sandbox,
            operation="memory_edit",
            allow_missing=False,
            memory_id=memory_id,
            content_b64=_b64(request),
        )

    async def delete(self, sandbox: Any, memory_id: str) -> dict[str, Any]:
        return await self.op(
            sandbox,
            operation="memory_delete",
            allow_missing=False,
            memory_id=memory_id,
        )


def _memory_entries(content: str) -> list[Any]:
    from fleet_rlm.workspace.models import parse_workspace_memory_lines

    lines = parse_workspace_memory_lines(content, complete_memory_graph=False)
    malformed = [line.raw for line in lines if line.malformed]
    if malformed:
        raise AssertionError(f"memory file corrupted by concurrent mutations: {malformed!r}")
    return [line for line in lines if line.entry is not None]


def _entry_ids(content: str) -> list[str]:
    return [str(line.entry.memory_id) for line in _memory_entries(content)]


def _flock_scripts(mount_path: str) -> tuple[str, str]:
    lock_path = f"{mount_path}/memory/MEMORIES.md.lock"
    holder = (
        "import fcntl, os, sys, time\n"
        f"os.makedirs(os.path.dirname({lock_path!r}) or '.', exist_ok=True)\n"
        f"fd = os.open({lock_path!r}, os.O_RDWR | os.O_CREAT, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('HOLDER_ACQUIRED', flush=True)\n"
        "time.sleep(20)\n"
        "print('HOLDER_RELEASED', flush=True)\n"
    )
    contender = (
        "import fcntl, os\n"
        f"fd = os.open({lock_path!r}, os.O_RDWR | os.O_CREAT, 0o600)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    print('CONTENDER_ACQUIRED', flush=True)\n"
        "    fcntl.flock(fd, fcntl.LOCK_UN)\n"
        "except BlockingIOError:\n"
        "    print('CONTENDER_BLOCKED', flush=True)\n"
    )
    return holder, contender


async def _live_scope() -> dict[str, Any]:
    from fleet_rlm.config import load_runtime_settings
    from fleet_rlm.daytona.platform import LiveDaytonaPlatform, build_daytona_client
    from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec
    from fleet_rlm.workspace.paths import DEFAULT_VOLUME_MOUNT_PATH

    _load_repo_env()
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for the shared-Volume Memory proof")
    if not os.environ.get("DAYTONA_API_KEY") and not os.environ.get("FLEET_DAYTONA_API_KEY"):
        pytest.fail("shared-Volume Memory proof requires DAYTONA_API_KEY credentials")
    settings = load_runtime_settings()
    client = build_daytona_client(settings)
    platform = LiveDaytonaPlatform(
        client,
        DaytonaSandboxSpec(snapshot=settings.daytona_snapshot or "fleet-rlm-python313-v5"),
    )
    return {
        "settings": settings,
        "client": client,
        "platform": platform,
        "mount_path": settings.volume_mount_path or DEFAULT_VOLUME_MOUNT_PATH,
        "subpath": f"workspaces/{uuid4()}",
        "volume_name": f"fleet-p19-memory-proof-{uuid4().hex[:12]}",
    }


async def _provision_peers(scope: dict[str, Any], count: int) -> list[Any]:
    from fleet_rlm.daytona.platform import LiveDaytonaVolumeClient

    client = scope["client"]
    volume = await LiveDaytonaVolumeClient(client).get(scope["volume_name"], create=True)
    volume_id = str(getattr(volume, "id", "") or "")
    if not volume_id:
        pytest.fail("volume client returned an object without id")
    scope["volume_id"] = volume_id
    peers = []
    for index in range(count):
        peers.append(
            await scope["platform"].create(
                volume_id=volume_id,
                mount_path=scope["mount_path"],
                volume_subpath=scope["subpath"],
                ephemeral=True,
                labels={"fleet.qre": "152", "probe_peer": str(index)},
            )
        )
    for peer in peers:
        await peer.process.code_run(
            f"import os\nos.makedirs({scope['mount_path']!r} + '/memory', exist_ok=True)\nprint('ok')",
            timeout=60,
        )
    return peers


async def _flock_primitive(sandbox_a: Any, sandbox_b: Any, mount_path: str) -> dict[str, Any]:
    holder_code, contender_code = _flock_scripts(mount_path)
    holder_task = asyncio.create_task(sandbox_a.process.code_run(holder_code, timeout=90))
    await asyncio.sleep(3)  # let the holder land the lock first
    contender = await sandbox_b.process.code_run(contender_code, timeout=90)
    try:
        holder = await holder_task
        holder_out = str(getattr(holder, "result", "") or "")
    except Exception as exc:
        holder_out = f"holder_error: {type(exc).__name__}"
    contender_out = str(getattr(contender, "result", "") or "")
    return {
        "holder_output": holder_out[-200:],
        "contender_output": contender_out[-200:],
        "contender_blocked": "CONTENDER_BLOCKED" in contender_out,
        "contender_acquired": "CONTENDER_ACQUIRED" in contender_out,
        "cross_mount_flock_coordinated": "CONTENDER_BLOCKED" in contender_out,
    }


async def _teardown(scope: dict[str, Any], peers: list[Any]) -> None:
    from fleet_rlm.daytona.lifecycle import confirm_absence

    client = scope["client"]
    platform = scope["platform"]
    for peer in peers:
        with contextlib.suppress(Exception):
            await platform.delete(peer.id)
    for peer in peers:
        with contextlib.suppress(Exception):
            await confirm_absence(
                probe=platform.get,
                sandbox_id=peer.id,
                timeout_s=180.0,
                poll_interval_s=2.0,
            )
    await client.close()


@pytest.mark.asyncio
async def test_live_shared_volume_memory_baseline_contracts() -> None:
    """Contracts that HOLD on the real shared Volume (with recorded evidence)."""
    scope = await _live_scope()
    peers = await _provision_peers(scope, 3)
    sandbox_a, sandbox_b, sandbox_c = peers
    receipt: dict[str, Any] = {
        "schema": _RECEIPT_SCHEMA,
        "case": "baseline-contracts",
        "volume": {"name": scope["volume_name"], "id": scope["volume_id"]},
        "scope": scope["subpath"],
        "sandbox_ids": [p.id for p in peers],
        "phases": {},
    }
    try:
        probe = _Probe(scope["mount_path"])

        # ---- Recorded primitive (evidence only; the falsification proof asserts on it) ----
        receipt["phases"]["flock_primitive"] = await _flock_primitive(sandbox_a, sandbox_b, scope["mount_path"])

        # ---- Sequential cross-Sandbox appends stay correct ----
        control_ids = [uuid4().hex[:8] for _ in range(4)]
        control_errors = []
        for i, mid in enumerate(control_ids):
            peer = sandbox_a if i % 2 == 0 else sandbox_b
            result = await probe.append(peer, _record(f"P19-control-{i}", mid))
            if not result.get("ok"):
                control_errors.append(result)
        content = await probe.read_memory(sandbox_a)
        present = set(_entry_ids(content))
        receipt["phases"]["sequential_append_control"] = {
            "expected": sorted(control_ids),
            "present": sorted(present),
            "errors": control_errors,
            "all_present": set(control_ids) <= present,
        }
        assert not control_errors and set(control_ids) <= present

        # ---- Mixed-op races: never corrupt, single occurrence, losers fail closed ----
        base_id = uuid4().hex[:8]
        second_id, third_id = uuid4().hex[:8], uuid4().hex[:8]
        await probe.append(sandbox_a, _record("P19 seed one", base_id))
        await probe.append(sandbox_b, _record("P19 seed two", second_id))
        await probe.append(sandbox_a, _record("P19 seed three", third_id))

        edit_res, append_res = await asyncio.gather(
            probe.edit(sandbox_a, base_id, "P19 edited under append race"),
            probe.append(sandbox_b, _record("P19 appended under edit race", uuid4().hex[:8])),
        )
        content = await probe.read_memory(sandbox_b)
        ids_now = _entry_ids(content)  # raises on corruption
        receipt["phases"]["edit_append"] = {
            "edit_ok": bool(edit_res.get("ok")),
            "append_ok": bool(append_res.get("ok")),
            "edit_observation": edit_res,
            "append_observation": append_res,
            "file_valid": True,
            "target_occurrences": ids_now.count(base_id),
        }
        assert ids_now.count(base_id) == 1

        del_res, edit_res2 = await asyncio.gather(
            probe.delete(sandbox_b, second_id),
            probe.edit(sandbox_a, third_id, "P19 edited during delete race"),
        )
        content = await probe.read_memory(sandbox_a)
        ids_now = _entry_ids(content)
        receipt["phases"]["edit_delete"] = {
            "delete_ok": bool(del_res.get("ok")),
            "edit_ok": bool(edit_res2.get("ok")),
            "delete_observation": del_res,
            "edit_observation": edit_res2,
            "file_valid": True,
            "other_occurrences": ids_now.count(third_id),
        }
        assert ids_now.count(third_id) == 1

        first, second = await asyncio.gather(
            probe.edit(sandbox_a, base_id, "P19 competing edit writer one"),
            probe.edit(sandbox_b, base_id, "P19 competing edit writer two"),
        )
        content = await probe.read_memory(sandbox_a)
        entries_content = _memory_entries(content)
        ids_now = _entry_ids(content)
        texts = [str(getattr(e.entry, "learning", "")) for e in entries_content if str(e.entry.memory_id) == base_id]
        receipt["phases"]["edit_edit"] = {
            "outcomes_ok_pair": (bool(first.get("ok")), bool(second.get("ok"))),
            "first_observation": first,
            "second_observation": second,
            "file_valid": True,
            "target_occurrences": ids_now.count(base_id),
            "final_learning_texts": texts,
        }
        assert ids_now.count(base_id) == 1
        assert len(set(texts)) == 1  # exactly one writer's text survives

        original = uuid4().hex[:8]
        await probe.append(sandbox_a, _record("P19 supersession original", original))
        sup1, sup2 = uuid4().hex[:8], uuid4().hex[:8]
        res1, res2 = await asyncio.gather(
            probe.append(sandbox_a, _record("P19 supersession writer one", sup1, supersedes_id=original)),
            probe.append(sandbox_b, _record("P19 supersession writer two", sup2, supersedes_id=original)),
        )
        content = await probe.read_memory(sandbox_b)
        _entry_ids(content)
        receipt["phases"]["supersession_race"] = {
            "outcomes_ok_pair": (bool(res1.get("ok")), bool(res2.get("ok"))),
            "first_observation": res1,
            "second_observation": res2,
            "file_valid": True,
        }

        # ---- Writer death mid-lock cannot wedge the Memory lock ----
        wedger = (
            "import fcntl, os\n"
            f"fd = os.open({scope['mount_path']!r} + '/memory/MEMORIES.md.lock', os.O_RDWR | os.O_CREAT, 0o600)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "os._exit(9)\n"
        )
        crash = await sandbox_a.process.code_run(wedger, timeout=60)
        retry_id = uuid4().hex[:8]
        after = await probe.append(sandbox_b, _record("P19 post-wedge append", retry_id))
        content = await probe.read_memory(sandbox_a)
        ids_now = _entry_ids(content)
        receipt["phases"]["writer_termination"] = {
            "crash_exit_code": getattr(crash, "exit_code", None),
            "post_wedge_append_ok": bool(after.get("ok")),
            "post_wedge_observation": after,
            "post_wedge_visible": retry_id in ids_now,
        }
        assert after.get("ok"), f"Memory lock wedged by writer death: {after}"
        assert retry_id in ids_now

        # ---- Third-Sandbox visibility of completed mutations ----
        ids_a = _entry_ids(await probe.read_memory(sandbox_a))
        ids_c = _entry_ids(await probe.read_memory(sandbox_c))
        receipt["phases"]["third_sandbox_visibility"] = {
            "third_sandbox_id": sandbox_c.id,
            "ids_from_a": sorted(ids_a),
            "ids_from_c": sorted(ids_c),
            "identical_view": sorted(ids_a) == sorted(ids_c),
        }
        assert sorted(ids_a) == sorted(ids_c)

        # ---- Replacement/fallback recording ----
        receipt["fallback_observations"] = probe.warnings
        receipt["observation_log_tail"] = probe.observations[-120:]
        receipt["fallback_warning_kinds"] = sorted({str(w.get("code")) for w in probe.warnings})
        assert probe.warnings  # the WORM ladder's fallback rung must be visible
        _write_receipt(receipt, suffix="memory-baseline")
    finally:
        await _teardown(scope, peers)


@pytest.mark.xfail(
    reason=(
        "FALSIFIED 2026-08-17: flock is not coordinated across mounts on the real Daytona "
        "Volume, so concurrent cross-Sandbox memory_appends silently lose records. XPASS "
        "signals the Fleet-side per-Workspace mutation lease landed; remove the mark then."
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_live_shared_volume_cross_sandbox_append_coordination() -> None:
    """The cross-Sandbox coordination invariant — currently falsified, never weakened.

    Asserts what the invariant REQUIRES: POSIX-level mutual exclusion between
    mounts and lossless concurrent append+append. Evidence is recorded to the
    receipt whether the assertion passes or is expectedly failing.
    """
    scope = await _live_scope()
    peers = await _provision_peers(scope, 2)
    sandbox_a, sandbox_b = peers
    receipt: dict[str, Any] = {
        "schema": _RECEIPT_SCHEMA,
        "case": "cross-sandbox-append-coordination",
        "volume": {"name": scope["volume_name"], "id": scope["volume_id"]},
        "scope": scope["subpath"],
        "sandbox_ids": [p.id for p in peers],
        "phases": {},
    }
    try:
        probe = _Probe(scope["mount_path"])
        primitive = await _flock_primitive(sandbox_a, sandbox_b, scope["mount_path"])
        receipt["phases"]["flock_primitive"] = primitive

        ids_a = [uuid4().hex[:8] for _ in range(3)]
        ids_b = [uuid4().hex[:8] for _ in range(3)]
        started = asyncio.get_running_loop().time()
        results = await asyncio.gather(
            *(
                [probe.append(sandbox_a, _record(f"P19-A-{i} concurrent append", mid)) for i, mid in enumerate(ids_a)]
                + [probe.append(sandbox_b, _record(f"P19-B-{i} concurrent append", mid)) for i, mid in enumerate(ids_b)]
            ),
            return_exceptions=True,
        )
        elapsed = asyncio.get_running_loop().time() - started
        content = await probe.read_memory(sandbox_a)
        present_list = _entry_ids(content)  # raises on corruption
        present = set(present_list)
        expected = set(ids_a + ids_b)
        receipt["phases"]["concurrent_append"] = {
            "elapsed_s": round(elapsed, 3),
            "agent_errors": [f"{r!r}" for r in results if isinstance(r, BaseException)]
            + [r for r in results if isinstance(r, dict) and not r.get("ok")],
            "expected": sorted(expected),
            "present": sorted(present),
            "lost": sorted(expected - present),
            "all_present": expected <= present,
        }
        receipt["fallback_observations"] = probe.warnings
        _write_receipt(receipt, suffix="memory-coordination")

        assert primitive["cross_mount_flock_coordinated"], f"flock not coordinated across mounts: {primitive}"
        assert expected <= present, f"concurrent append+append lost records: {receipt['phases']['concurrent_append']}"
    finally:
        await _teardown(scope, peers)
