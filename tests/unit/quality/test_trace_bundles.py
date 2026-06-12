from __future__ import annotations

import json
from pathlib import Path

from fleet_rlm.quality.trace_bundles import (
    distill_trace_payloads,
    write_session_trace_artifacts,
)


def test_distill_trace_payloads_clusters_failures() -> None:
    rows, summary = distill_trace_payloads(
        [
            {
                "info": {"trace_id": "tr-1", "client_request_id": "chat-1"},
                "session_id": "session-1",
                "spans": [{"span_type": "TOOL", "status": "error"}],
                "assessments": [{"assessment_name": "response_is_correct"}],
                "metadata": {"detail": "missing evidence and malformed json"},
            }
        ]
    )

    assert rows[0]["trace_id"] == "tr-1"
    assert "missing_evidence" in rows[0]["failure_categories"]
    assert "formatting_issues" in rows[0]["failure_categories"]
    assert summary["trace_count"] == 1
    assert summary["failure_clusters"]


def test_write_session_trace_artifacts_writes_raw_and_distilled(tmp_path: Path) -> None:
    artifacts = write_session_trace_artifacts(
        session_id="session-1",
        payloads=[{"info": {"trace_id": "tr-1"}, "spans": [], "assessments": []}],
        export_format="both",
        root=tmp_path,
    )

    assert Path(artifacts["json_path"] or "").exists()
    assert Path(artifacts["jsonl_path"] or "").exists()
    distilled_path = Path(artifacts["distilled_bundle_path"])
    assert distilled_path.exists()
    first_row = json.loads(distilled_path.read_text(encoding="utf-8").splitlines()[0])
    assert first_row["kind"] == "trace_bundle_summary"
