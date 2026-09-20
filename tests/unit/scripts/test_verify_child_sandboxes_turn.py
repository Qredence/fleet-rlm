"""Safety contract for the operator-gated recursive-child verifier."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_child_sandboxes_turn.py"
_LIVE_CANARY = Path(__file__).resolve().parents[3] / "tests" / "live" / "backend" / "test_daytona_recursive_batch.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_child_sandboxes_turn", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_module():
    spec = importlib.util.spec_from_file_location("test_daytona_recursive_batch", _LIVE_CANARY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_help_is_side_effect_free() -> None:
    result = subprocess.run([sys.executable, str(_SCRIPT), "--help"], capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "--output" in result.stdout


def test_live_authorization_is_required_before_pytest(tmp_path: Path) -> None:
    environment = {**os.environ, "FLEET_LIVE": ""}
    output = tmp_path / "receipt.json"

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--output", str(output)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode != 0
    assert "FLEET_LIVE=1" in result.stderr
    assert not output.exists()


def test_receipt_validation_requires_trace_hierarchy(tmp_path: Path) -> None:
    module = _module()
    output = tmp_path / "receipt.json"
    output.write_text(
        '{"schema":"fleet.p35d-root-batch/v1","passed":true,'
        '"cleanup":{"confirmed_absent":true,"admission_restored":true},'
        '"assertions":{"native_child_count":2,"ordered_root_batch":true,'
        '"peak_child_concurrency":2,"retained_root_second_turn":true},'
        '"trace":{"root_span":"fleet_turn","trace_id":"tr-1","child_spans":1}}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="root and child traces"):
        module._validate_receipt(output)


def test_receipt_validation_accepts_complete_evidence(tmp_path: Path) -> None:
    module = _module()
    output = tmp_path / "receipt.json"
    output.write_text(
        '{"schema":"fleet.p35d-root-batch/v1","passed":true,'
        '"cleanup":{"confirmed_absent":true,"admission_restored":true},'
        '"assertions":{"native_child_count":2,"ordered_root_batch":true,'
        '"peak_child_concurrency":2,"retained_root_second_turn":true},'
        '"trace":{"root_span":"fleet_turn","trace_id":"tr-1","child_spans":2}}',
        encoding="utf-8",
    )

    module._validate_receipt(output)


def test_trace_hierarchy_rejects_wrong_parentage() -> None:
    module = _live_module()
    trace_id = "tr-1"
    trace = SimpleNamespace(
        data=SimpleNamespace(
            spans=[
                SimpleNamespace(name="fleet_turn", trace_id=trace_id, span_id="root", parent_id=None),
                SimpleNamespace(name="RLM.recursive_call", trace_id=trace_id, span_id="child-1", parent_id="other"),
                SimpleNamespace(name="RLM.recursive_call", trace_id=trace_id, span_id="child-2", parent_id="other"),
            ]
        )
    )

    with pytest.raises(AssertionError, match="parent chain"):
        module._trace_hierarchy(trace, trace_id=trace_id)


def test_trace_hierarchy_accepts_intermediate_parent_spans() -> None:
    module = _live_module()
    trace_id = "tr-1"
    trace = SimpleNamespace(
        data=SimpleNamespace(
            spans=[
                SimpleNamespace(name="fleet_turn", trace_id=trace_id, span_id="root", parent_id=None),
                SimpleNamespace(name="tool_call", trace_id=trace_id, span_id="tool", parent_id="root"),
                SimpleNamespace(name="RLM.recursive_call", trace_id=trace_id, span_id="child-1", parent_id="tool"),
                SimpleNamespace(name="RLM.recursive_call", trace_id=trace_id, span_id="child-2", parent_id="tool"),
            ]
        )
    )

    assert module._trace_hierarchy(trace, trace_id=trace_id)["child_spans"] == 2


def test_bounded_mlflow_call_times_out() -> None:
    module = _live_module()

    with pytest.raises(TimeoutError):
        module._bounded_call(lambda: time.sleep(0.2), timeout=0.01)
