from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.benchmarks import capture_runtime_baseline as capture


def _result() -> dict[str, object]:
    return {
        "schema": "fleet.runtime-benchmark/v1",
        "scenario": "exact-calculation",
        "runtime_mode": "legacy",
        "root_model": "root-model",
        "sub_model": "sub-model",
        "turn_duration_ms": 1,
        "provider_attempts": 1,
        "root_action_calls": 1,
        "sub_lm_calls": 0,
        "child_root_calls": 0,
        "child_sub_lm_calls": 0,
        "parse_repairs": 0,
        "tool_calls": 0,
        "recursive_calls": 0,
        "child_sandboxes": 0,
        "delegated_context_chars": 0,
        "terminal_status": "succeeded",
        "score": 1,
    }


def test_capture_writes_strict_content_free_receipt(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "results.json"
    output = tmp_path / "baseline.json"
    source.write_text(json.dumps([_result()]), encoding="utf-8")
    config = tmp_path / "fleet.toml"
    config.write_text("policy", encoding="utf-8")
    settings = type(
        "Settings",
        (),
        {"daytona_snapshot": "fleet-rlm-python313-v5", "root_model": "root-model", "sub_model": "sub-model"},
    )()
    monkeypatch.setattr(capture, "_CONFIG_PATH", config)
    monkeypatch.setattr(capture, "load_runtime_settings", lambda: settings)
    monkeypatch.setattr(capture, "_commit", lambda: "a" * 40)

    assert capture.main(["--results", str(source), "--output", str(output)]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["config_digest"] == hashlib.sha256(b"policy").hexdigest()
    assert receipt["results"][0]["scenario"] == "exact-calculation"
    assert "prompt" not in json.dumps(receipt)
