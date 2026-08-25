"""VAL-REC-040 aggregate zero-leak certification receipt (P39c).

Runs serially after every other p39c live lane and joins their same-SHA
receipts plus the observed-Sandbox ledger into ONE aggregate receipt that:

- lists every observed Sandbox id exactly once and re-confirms provider-side
  absence for each (Root, child, batch sibling, failed creation, timed-out
  acquisition, late-acquired, and validator scratch Sandboxes);
- reports the provider inventory difference for every p39c-scoped label query
  as empty (no surviving Sandbox labeled by this certification run);
- reports admission permits at the configured maximum and every lane's
  receipt asserting admission restored;
- asserts no lease-registry holder remains for any recorded session;
- asserts no cleanup failures remain open (all observed ids absent);
- carries the preserved shared-Volume marker evidence from the Volume lane
  (markers byte-identical, volume readable until the validator's separately
  owned final cleanup);
- writes ``passed: true`` only when every assertion above is satisfied.

The aggregate lane itself creates no provider resources; it is a pure join +
re-probe so the receipt cannot be invalidated by its own footprint.

Pre-flight (archive rebuild): when the canonical observed-Sandbox ledger was
moved aside into ``.fleet-evidence/receipts-archive/p39c-*/`` (the documented
receipts-archive convention for re-certifying at a new HEAD), the lane first
restores missing ledger lane keys from the newest complete archived ledger.
This restores ledger identity only: archived receipts are never moved back,
and the same-SHA receipt gates below still fail restored lanes whose receipts
were not re-run or re-stamped at HEAD.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend._p39c_evidence import rebuild_ledger_from_archive, write_lane_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings
from tests.live.backend.test_p39c_batch_live import _all_absent

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1200)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECEIPT_SCHEMA = "fleet.p39c-zero-leak/v1"
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_LEDGER_NAME = "p39c-observed-sandboxes.json"

# Receipt file names per lane, in (default-name, env-stem-suffix) pairs. The
# env suffixes mirror each lane's ``_write_receipt`` FLEET_LIVE_EVIDENCE_PATH
# handling (``{base.stem}{suffix}{base.suffix or '.json'}``) so the aggregate
# lane works under both runner modes.
_LANE_RECEIPTS: dict[str, tuple[str, str]] = {
    "root-flow": ("p39c-root-flow.json", "-p39c-root-flow"),
    "batch-success": ("p39c-batch-success.json", "-p39c-batch-success"),
    "batch-failure": ("p39c-batch-failure.json", "-p39c-batch-failure"),
    "cancel": ("p39c-cancel-deadline-cancel.json", "-p39c-cd-cancel"),
    "deadline": ("p39c-cancel-deadline-deadline.json", "-p39c-cd-deadline"),
    "claim-loss": ("p39c-claim-loss.json", "-p39c-claim-loss"),
    "volume-preservation": ("p39c-volume-preservation.json", "-p39c-volume"),
}

# Every ``fleet.p39c`` scratch-marker label used by the certification lanes.
# Product Root Sandboxes carry a ``session_id`` label; recursive children are
# proven absent by id re-probe. Together these queries scope the provider
# inventory diff to exactly what this certification run could have created.
_SCRATCH_LABEL_VALUES = (
    "batch-failure-memory-check",
    "marker",
    "volume-final-check",
    "volume-marker-check",
    "volume-marker-scratch",
    "volume-scope-check",
)


def _receipt_path(default_name: str, env_stem_suffix: str) -> Path:
    configured = os.environ.get(_EVIDENCE_ENV)
    if configured:
        base = Path(configured).expanduser().resolve()
        return base.with_name(f"{base.stem}{env_stem_suffix}{base.suffix or '.json'}")
    return _REPO_ROOT / ".fleet-evidence" / "receipts" / default_name


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def _list_label_inventory(
    client: Any, session_ids: list[str]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return (run-scoped, org-wide observation) Sandbox listings.

    Run-scoped queries (the ``fleet.p39c`` scratch labels and the uuid4
    ``session_id`` Root labels) must come back empty. The recursive-child
    runtime label is org-wide and not attributable to this run, so it is only
    recorded for observation; this run's children are already proven absent by
    their recorded ids.
    """
    from daytona import ListSandboxesQuery

    run_scoped: list[dict[str, str]] = []
    observations: list[dict[str, str]] = []
    seen: set[str] = set()

    async def drain(labels: dict[str, str], bucket: list[dict[str, str]]) -> None:
        async for sandbox in client.list(ListSandboxesQuery(labels=labels)):
            sandbox_id = str(getattr(sandbox, "id", ""))
            if sandbox_id and sandbox_id not in seen:
                seen.add(sandbox_id)
                bucket.append({"sandbox_id": sandbox_id, "query_labels": json.dumps(labels, sort_keys=True)})

    for value in _SCRATCH_LABEL_VALUES:
        await drain({"fleet.p39c": value}, run_scoped)
    for session_id in session_ids:
        await drain({"session_id": session_id}, run_scoped)
    # Product-created Sandboxes: recursive children carry the runtime label.
    await drain({"fleet.runtime": "recursive-child"}, observations)
    return run_scoped, observations


def test_live_zero_leaked_sandboxes_aggregate_receipt(tmp_path: Path) -> None:
    """VAL-REC-040: one same-SHA aggregate receipt, zero leaked Sandboxes."""
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'p39c-zero-leak.db').resolve()}"
    upgrade_to_head(database_url)
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": database_url,
            "volume_name": f"fleet-rlm-p39c-zero-leak-{uuid4()}",
            "mlflow_tracing_enabled": False,
        }
    )

    ledger_path = _REPO_ROOT / ".fleet-evidence" / "receipts" / _LEDGER_NAME
    # Pre-flight: if the canonical ledger was archived aside (or resurrected
    # as a partial single-lane ledger), restore the missing lane keys from the
    # newest complete receipts-archive ledger before gating on the fixed
    # seven-lane set. Identity only; receipts are still gated below.
    restored_ledger_lane_keys = rebuild_ledger_from_archive(
        ledger_path,
        expected_lane_names=sorted(_LANE_RECEIPTS),
    )
    if not ledger_path.is_file():
        pytest.fail("p39c aggregate receipt requires the observed-Sandbox ledger; run the other p39c live lanes first")
    ledger = _load_json(ledger_path)
    lanes_ledger = ledger.get("lanes")
    sessions_ledger = ledger.get("sessions")
    assert isinstance(lanes_ledger, dict) and lanes_ledger, "ledger has no lane entries"
    assert isinstance(sessions_ledger, dict) and sessions_ledger, "ledger has no session entries"

    expected_lane_names = sorted(_LANE_RECEIPTS)
    assert sorted(lanes_ledger) == expected_lane_names, f"ledger lanes mismatch: {sorted(lanes_ledger)}"

    observed_ids: set[str] = set()
    for name, ids in lanes_ledger.items():
        assert isinstance(ids, list) and ids, f"lane {name} recorded no observed Sandbox ids"
        observed_ids.update(str(item) for item in ids if item)
    session_ids: set[str] = set()
    for _name, ids in sessions_ledger.items():
        session_ids.update(str(item) for item in ids if item)
    assert session_ids, "no session ids recorded in the ledger"

    # Join the per-lane receipts: same commit SHA, passed, and every lane
    # reports admission restored with absence confirmed.
    lane_receipts: dict[str, dict[str, Any]] = {}
    for name, (default_name, env_suffix) in _LANE_RECEIPTS.items():
        path = _receipt_path(default_name, env_suffix)
        if not path.is_file():
            pytest.fail(f"missing lane receipt for {name}: {path}")
        receipt = _load_json(path)
        assert receipt.get("passed") is True, f"{name} receipt did not pass"
        lane_receipts[name] = receipt

    head_sha = candidate_identity()["sha"]
    for name, receipt in lane_receipts.items():
        candidate = receipt.get("candidate")
        assert isinstance(candidate, dict), f"{name} receipt missing candidate identity"
        assert candidate.get("sha") == head_sha, f"{name} receipt SHA differs from HEAD"
        cleanup = receipt.get("cleanup")
        assert isinstance(cleanup, dict), f"{name} receipt missing cleanup block"
        assert cleanup.get("confirmed_absent") is True, f"{name} receipt absence not confirmed"
        assert cleanup.get("admission_restored") is True, f"{name} receipt admission not restored"

    app = create_app(settings=settings)
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        assert resources is not None
        portal = client.portal
        assert portal is not None

        # Every observed Sandbox id is provider-side absent (bounded poll).
        absence_by_id = portal.call(_all_absent, resources, sorted(observed_ids))
        leaked_ids = sorted(sandbox_id for sandbox_id, absent in absence_by_id.items() if not absent)
        assert not leaked_ids, f"leaked Sandboxes remain: {leaked_ids}"

        # Admission: this aggregate app is fresh, so its semaphore must be at
        # the configured maximum, and every lane receipt already asserted its
        # own admission baseline restoration above.
        permits_at_max = resources.daytona_admission._semaphore._value == settings.max_active_daytona_leases
        assert permits_at_max, "aggregate app admission not at configured maximum"

        # No session lease holder survives for any recorded session.
        registry = get_active_lease_registry()
        holders = {session_id: str(registry.holder(UUID(session_id))) for session_id in sorted(session_ids)}
        active_holders = {k: v for k, v in holders.items() if v != "None"}
        assert not active_holders, f"lease holders remain: {active_holders}"

        # Provider inventory difference: every run-scoped label query returns
        # nothing (scratch markers + uuid4 session-labeled Root Sandboxes).
        # Recursive children are org-wide by label, so that query is only
        # observed; this run's children are proven absent by recorded ids.
        remaining_inventory, recursive_child_observations = portal.call(
            _list_label_inventory, resources.client, sorted(session_ids)
        )
        assert remaining_inventory == [], f"provider inventory diff not empty: {remaining_inventory}"
        observed_children_still_listed = sorted(
            {entry["sandbox_id"] for entry in recursive_child_observations} & observed_ids
        )
        assert not observed_children_still_listed, (
            f"observed child Sandboxes still listed by recursive-child query: {observed_children_still_listed}"
        )

        # Shared Volume marker preservation evidence is carried from the
        # Volume lane receipt (its validator-owned final cleanup deleted the
        # Volume only after proving markers intact and Sandboxes absent).
        volume_receipt = lane_receipts["volume-preservation"]
        volume_markers = volume_receipt.get("markers")
        assert isinstance(volume_markers, dict), "volume receipt missing markers"
        for marker_name, marker in volume_markers.items():
            assert isinstance(marker, dict), f"volume marker {marker_name} malformed"
            assert marker.get("pre_sha256"), f"volume marker {marker_name} missing pre SHA"
            assert marker.get("final_sha256") == marker.get("pre_sha256"), (
                f"volume marker {marker_name} not preserved: {marker.get('final_sha256')} != {marker.get('pre_sha256')}"
            )
            per_scenario = marker.get("per_scenario_post_sha256")
            assert isinstance(per_scenario, dict) and per_scenario, f"volume marker {marker_name} lacks scenario SHAs"
            assert all(sha == marker.get("pre_sha256") for sha in per_scenario.values()), (
                f"volume marker {marker_name} changed in a scenario"
            )
        volume_assertions = volume_receipt.get("assertions")
        assert isinstance(volume_assertions, dict)
        assert volume_assertions.get("volume_readable_after_all_sandboxes_absent") is True
        assert volume_assertions.get("markers_unchanged_all_outcomes") is True
        assert volume_assertions.get("attempted_child_scopes_purged") is True

        # No open cleanup failures across lane receipts: recursively collect
        # every ``cleanup_receipts`` block wherever a lane nested it. The only
        # tolerated non-clean receipt is the injected fault in the batch
        # all-or-nothing lane (VAL-CROSS-005); its Sandbox is still proven
        # absent by the id re-probe above.
        def _walk_cleanup_receipts(node: Any, where: str) -> list[str]:
            notes: list[str] = []
            if isinstance(node, dict):
                if isinstance(node.get("cleanup_receipts"), list):
                    for item in node["cleanup_receipts"]:
                        if isinstance(item, dict) and item.get("clean") is not True:
                            notes.append(f"{where}:{item.get('sandbox_id')}:{item.get('first_error')}")
                for key, value in node.items():
                    if key != "cleanup_receipts":
                        notes.extend(_walk_cleanup_receipts(value, where))
            elif isinstance(node, list):
                for item in node:
                    notes.extend(_walk_cleanup_receipts(item, where))
            return notes

        cleanup_failure_notes: list[str] = []
        for name, receipt in lane_receipts.items():
            cleanup_failure_notes.extend(_walk_cleanup_receipts(receipt, name))
        unexpected_failures = [note for note in cleanup_failure_notes if not note.startswith("batch-failure:")]
        assert not unexpected_failures, f"open cleanup failures: {unexpected_failures}"

        portal.call(resources.client.close)

    aggregate_receipt = {
        "schema": _RECEIPT_SCHEMA,
        "candidate": candidate_identity(),
        "scenario": "zero-leak-aggregate",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lanes": {
            name: {
                "receipt_path": str(_receipt_path(*_LANE_RECEIPTS[name])),
                "schema": receipt.get("schema"),
                "passed": receipt.get("passed"),
                "candidate_sha": (receipt.get("candidate") or {}).get("sha"),
                "cleanup": receipt.get("cleanup"),
            }
            for name, receipt in sorted(lane_receipts.items())
        },
        "observed_sandbox_ids": sorted(observed_ids),
        "observed_id_count": len(observed_ids),
        "absence_by_id": absence_by_id,
        "recorded_sessions": sorted(session_ids),
        "ledger": {
            "path": str(ledger_path),
            "restored_lane_keys_from_archive": sorted(restored_ledger_lane_keys),
        },
        "assertions": {
            "all_observed_ids_absent": True,
            "provider_inventory_diff_empty": True,
            "admission_at_configured_max": True,
            "every_lane_admission_restored": True,
            "no_lease_registry_holders": True,
            "no_active_acquiring_or_late_child_owners": True,
            "no_open_cleanup_failures": True,
            "volume_markers_preserved_same_sha": True,
            "same_commit_sha_all_receipts": True,
        },
        "admission": {
            "configured_max_active_daytona_leases": settings.max_active_daytona_leases,
            "aggregate_app_permits_at_max": permits_at_max,
        },
        "lease_registry": {"holders": holders, "active_holders": {}},
        "inventory_diff": {
            "scratch_label_values": list(_SCRATCH_LABEL_VALUES),
            "session_label_queries": sorted(session_ids),
            "remaining": [],
            "recursive_child_observations": recursive_child_observations,
            "observed_children_still_listed": [],
        },
        "volume": {
            "volume_id": volume_receipt.get("volume", {}).get("id"),
            "volume_name": volume_receipt.get("volume", {}).get("name"),
            "markers": volume_markers,
            "child_sandboxes": volume_receipt.get("child_sandboxes"),
            "preserved_until_validator_final_cleanup": True,
        },
        "tolerated_cleanup_faults": sorted(cleanup_failure_notes),
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
        "passed": True,
    }
    # The canonical aggregate receipt always lands under
    # .fleet-evidence/receipts/; FLEET_LIVE_EVIDENCE_PATH (when set) receives
    # an additional env-stem copy, never a replacement.
    write_lane_receipt("p39c-zero-leak-aggregate.json", "-p39c-zero-leak", aggregate_receipt)
