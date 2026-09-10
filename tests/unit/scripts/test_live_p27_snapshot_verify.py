"""Unit contracts for the aggregate P2.7 Daytona snapshot certification lane."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import live_p27_snapshot_verify as verifier


def test_rejects_existing_or_outside_receipts_without_live_setup(tmp_path: Path) -> None:
    output = tmp_path / "existing.json"
    output.write_text("{}", encoding="utf-8")

    assert (
        verifier.main(
            [
                "--session-snapshot",
                "fleet-rlm-python313-v10",
                "--child-snapshot",
                "fleet-rlm-python313-child-v5",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert output.read_text(encoding="utf-8") == "{}"


def test_seals_aggregate_receipt_after_both_existing_lanes(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "receipt.json"
    images = {
        "session": {
            "snapshot": "fleet-rlm-python313-v10",
            "manifest_sha256": "a" * 64,
            "dependency_sha256": "b" * 64,
            "resources": {"cpu": 4, "memory_gib": 8, "disk_gib": 8},
        },
        "semantic-child": {
            "snapshot": "fleet-rlm-python313-child-v5",
            "manifest_sha256": "c" * 64,
            "dependency_sha256": "d" * 64,
            "resources": {"cpu": 2, "memory_gib": 4, "disk_gib": 4},
        },
    }
    monkeypatch.setattr(verifier, "_allowed_output", lambda path: path == output)
    monkeypatch.setattr(verifier, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(verifier, "require_live_execution", lambda: object())
    monkeypatch.setattr(verifier, "_candidate", lambda: ("a" * 40, "p27-cert"))
    monkeypatch.setattr(verifier, "_RECURSIVE_EVIDENCE_ROOT", tmp_path / "recursive")
    monkeypatch.setattr(verifier, "_run", lambda *_args: None)
    monkeypatch.setattr(verifier, "_assert_success_receipt", lambda _path: None)

    def fake_asyncio_run(coro):
        coro.close()
        return images

    monkeypatch.setattr(verifier.asyncio, "run", fake_asyncio_run)
    assert (
        verifier.main(
            [
                "--session-snapshot",
                "fleet-rlm-python313-v10",
                "--child-snapshot",
                "fleet-rlm-python313-child-v5",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["passed"] is True
    assert receipt["images"] == images
