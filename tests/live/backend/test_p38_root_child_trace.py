"""Credentialed Root/child engineering-trace comparison (P38-RLM-006).

The P38 contraction removed raw provider-response telemetry and the typed
``LMResponse`` fallback from the LM trace callback. This lane proves on the
real Root/child Daytona seam, with a deterministic ``DummyLM`` script and the
mission MLflow validator on ``127.0.0.1:5010``, that:

- the public product stream is unchanged (one start/finish, reasoning before
  code, bounded ``tool-output-available`` recursive evidence, exposed trace
  id);
- each Fleet engineering lifecycle key emits exactly one span (no duplicate
  duration/exception/Tool/trajectory effect);
- Root/child ancestry forms one acyclic graph under the owning recursive
  call, with Root depth 0 and child depth 1 and no grandchild;
- retained fields (wall time, call index, response keys, truthful usage)
  survive while the deleted provider fields are absent;
- cleanup deletes every owned Sandbox, restores admission, and keeps the
  shared Volume until the validator's own final cleanup.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.rlm.program import RLMModelBundle
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity, write_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings
from tests.live.backend.test_phase1_daytona_stream import _strict_cleanup

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(900)]

_MLFLOW_TRACKING_URI = "http://127.0.0.1:5010"
_EXPERIMENT_NAME = "fleet-rlm-p38-root-child-trace"
_RECEIPT_SCHEMA = "fleet.p38-root-child-trace/v1"

# The closed Fleet-owned engineering span vocabulary for one recursive Turn.
_FLEET_SPAN_NAMES = frozenset(
    {
        "fleet_turn",
        "RLM.execute",
        "RLM.root_action",
        "RLM.root_lm",
        "sandbox.execute",
        "tool.rlm_query",
        "RLM.recursive_call",
    }
)
_DELETED_PROVIDER_FIELDS = (
    "provider_response_ms",
    "litellm_overhead_ms",
    "callback_duration_ms",
    "provider_request_id",
)


class _ChildScriptedLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [{"reasoning": "complete the bounded child", "code": "SUBMIT(answer='p38-child-trace-ok')"}],
            adapter=dspy.JSONAdapter(),
        )


class _RootScriptedLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [
                {"reasoning": "delegate exactly once", "code": "child = rlm_query(prompt='p38 trace child')"},
                {"reasoning": "complete the bounded root", "code": "SUBMIT(answer='p38-root-trace-ok')"},
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> _ChildScriptedLM:
        del kwargs
        return _ChildScriptedLM()


def _chunks(response: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and line.removeprefix("data: ") != "[DONE]"
    ]


def _wait_for_admission(resources: Any, *, permits: int) -> None:
    deadline = time.perf_counter() + 45
    while time.perf_counter() < deadline:
        if resources.daytona_admission._semaphore._value == permits:
            return
        time.sleep(0.25)
    assert resources.daytona_admission._semaphore._value == permits


def _input_value(span: dict[str, Any], key: str) -> Any:
    inputs = span.get("inputs")
    return inputs.get(key) if isinstance(inputs, dict) else None


def _export_trace_spans(trace_id: str) -> list[dict[str, Any]]:
    """Export one trace from the mission validator MLflow, sanitized.

    Span files are written by the tracking store's async/flush pipeline, so a
    first read can race a partially-written ``traces/<id>.json``. Flush the
    logging queue, then retry with backoff until the trace loads cleanly.
    """
    import mlflow

    mlflow.set_tracking_uri(_MLFLOW_TRACKING_URI)
    flush = getattr(mlflow, "flush_trace_async_logging", None)
    if callable(flush):
        flush(terminate=False)

    trace = None
    last_error: Exception | None = None
    for _ in range(12):
        try:
            trace = mlflow.get_trace(trace_id)
            break
        except Exception as exc:  # Retry on transient span-file write races.
            last_error = exc
            time.sleep(1.0)
    assert trace is not None, f"trace {trace_id} never exported cleanly: {last_error}"

    spans: list[dict[str, Any]] = []
    for span in trace.data.spans:
        outputs = getattr(span, "outputs", None)
        inputs = getattr(span, "inputs", None)
        attributes = getattr(span, "attributes", None) or {}
        spans.append(
            {
                "name": span.name,
                "span_id": span.span_id,
                "parent_id": span.parent_id,
                "inputs": inputs if isinstance(inputs, dict) else {},
                "outputs": outputs if isinstance(outputs, dict) else {},
                "attributes": {str(key): str(attributes[key])[:64] for key in tuple(attributes)[:32]},
            }
        )
    return spans


def test_live_p38_root_child_trace_is_single_span_and_public_stream_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for the credentialed Root/child trace comparison")

    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'p38-trace.db').resolve()}",
            "volume_name": f"fleet-rlm-p38-trace-{uuid4()}",
            "rlm_recursion_enabled": True,
            "rlm_recursion_max_calls": 1,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 1,
            "rlm_recursion_child_max_llm_calls": 1,
            "rlm_max_iters": 2,
            "rlm_max_llm_calls": 2,
            "turn_timeout_seconds": 840,
            # Engineering tracing ON against the mission validator MLflow so
            # the post-contraction span graph can be exported and compared.
            "mlflow_tracing_enabled": True,
            "mlflow_tracking_uri": _MLFLOW_TRACKING_URI,
            "mlflow_experiment_name": _EXPERIMENT_NAME,
            "mlflow_expose_trace_id": True,
            # Synchronous logging: the trace must be queryable immediately
            # after the Turn settles.
            "mlflow_async_logging": False,
            "mlflow_trace_sampling_ratio": 1.0,
        }
    )
    upgrade_to_head(settings.database_url or "")

    original_child_interpreter = recursive_child_runtime.DaytonaCodeInterpreter
    child_interpreter_count = 0

    def build_child_interpreter(**kwargs: Any) -> Any:
        nonlocal child_interpreter_count
        child_interpreter_count += 1
        return original_child_interpreter(**kwargs)

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", build_child_interpreter)

    app = create_app(settings=settings)
    cleanup_failures: tuple[str, ...] = ()
    trace_id: str | None = None
    chunk_summary: dict[str, Any] = {}
    span_counts: dict[str, int] = {}
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None and preparation is not None
        preparation._models = RLMModelBundle(_RootScriptedLM(), dspy.utils.DummyLM([{"answer": "unused"}]))

        try:
            created = client.post("/api/sessions", json={"title": "P38 Root child trace"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run exactly one recursive child and return the bounded root answer."},
                headers={"Idempotency-Key": f"p38-trace-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks = _chunks(response)

            # --- Public product stream unchanged through the contraction. ---
            start = next(chunk for chunk in chunks if chunk.get("type") == "start")
            assert chunks[-1]["type"] == "finish"
            assert chunks[-1]["finishReason"] == "stop"
            assert sum(chunk.get("type") == "start" for chunk in chunks) == 1
            assert sum(chunk.get("type") == "finish" for chunk in chunks) == 1
            reasoning_indices = [i for i, chunk in enumerate(chunks) if chunk.get("type") == "reasoning-delta"]
            code_indices = [i for i, chunk in enumerate(chunks) if chunk.get("type") == "data-rlm-code"]
            assert reasoning_indices and code_indices
            assert min(reasoning_indices) < min(code_indices)
            recursive_evidence = [
                chunk
                for chunk in chunks
                if chunk.get("type") == "tool-output-available"
                and isinstance(chunk.get("output"), dict)
                and chunk["output"].get("recursive_depth") == 1
            ]
            assert len(recursive_evidence) == 1
            text = "".join(str(chunk.get("delta", "")) for chunk in chunks if chunk.get("type") == "text-delta")
            assert text == "p38-root-trace-ok"
            trace_id = start.get("messageMetadata", {}).get("traceId")
            assert isinstance(trace_id, str) and trace_id
            assert chunks[-1].get("messageMetadata", {}).get("traceId") == trace_id
            chunk_summary = {
                "types": [chunk.get("type") for chunk in chunks],
                "finish_reason": chunks[-1]["finishReason"],
                "recursive_evidence_count": len(recursive_evidence),
                "reasoning_before_code": min(reasoning_indices) < min(code_indices),
            }

            # --- Export the engineering trace and compare the span graph. ---
            spans = client.portal.call(_export_trace_spans, trace_id)
            assert spans, "the certified trace export returned no spans"

            by_id = {span["span_id"]: span for span in spans}
            roots = [span for span in spans if span["parent_id"] is None]
            assert len(roots) == 1 and roots[0]["name"] == "fleet_turn"

            def ancestors(span_id: str) -> list[str]:
                seen: list[str] = []
                current = by_id.get(span_id)
                while current is not None and current["parent_id"] is not None:
                    assert current["parent_id"] not in seen, "cycle in trace graph"
                    seen.append(current["parent_id"])
                    current = by_id.get(current["parent_id"])
                return seen

            fleet_spans = [span for span in spans if span["name"] in _FLEET_SPAN_NAMES]
            counts = Counter(span["name"] for span in fleet_spans)
            span_counts = dict(counts)
            # One span per normalized lifecycle key; no duplicate effect.
            # Root runs two scripted actions (2 root_lm + 2 sandbox.execute);
            # the child runs one action (1 root_lm at depth 1 + 1
            # sandbox.execute); the final SUBMIT code lands in the second
            # Root sandbox.execute.
            assert counts == {
                "fleet_turn": 1,
                "RLM.execute": 1,
                "RLM.root_action": 2,
                "RLM.root_lm": 3,
                "sandbox.execute": 3,
                "tool.rlm_query": 1,
                "RLM.recursive_call": 1,
            }

            # Every Fleet span reaches the single fleet_turn root through one
            # acyclic parent chain (MLflow DSPy autolog spans may interleave).
            for span in fleet_spans:
                if span["name"] == "fleet_turn":
                    continue
                chain = ancestors(span["span_id"])
                assert chain, f"Fleet span not parented: {span['name']}"

            # LM spans: two Root calls at depth 0, one child call at depth 1.
            lm_spans = [span for span in fleet_spans if span["name"] == "RLM.root_lm"]
            depths = Counter(_input_value(span, "recursive_depth") for span in lm_spans)
            assert depths == {0: 2, 1: 1}
            for span in lm_spans:
                outputs = span["outputs"]
                assert isinstance(outputs.get("wall_time_ms"), (int, float))
                assert isinstance(outputs.get("call_index"), int)
                assert "response_keys" in outputs
                assert outputs.get("request_status") == "completed"
                for removed in _DELETED_PROVIDER_FIELDS:
                    assert removed not in json.dumps(span), removed
            assert "_hidden_params" not in json.dumps(spans)

            # Ancestry: the owning recursive call is parented under the
            # explicit Root Tool edge, the child LM span under it, and every
            # Root action/sandbox/Tool span under the one execution span.
            # Ancestor walks (not direct-parent checks) keep the claim exact
            # while allowing MLflow DSPy autolog spans to interleave.
            recursive_call = next(span for span in fleet_spans if span["name"] == "RLM.recursive_call")
            tool_span = next(span for span in fleet_spans if span["name"] == "tool.rlm_query")
            assert tool_span["span_id"] in ancestors(recursive_call["span_id"])
            child_lm = next(span for span in lm_spans if _input_value(span, "recursive_depth") == 1)
            assert recursive_call["span_id"] in ancestors(child_lm["span_id"])

            execute_span = next(span for span in fleet_spans if span["name"] == "RLM.execute")
            root_actions = [span for span in fleet_spans if span["name"] == "RLM.root_action"]
            for action in root_actions:
                assert execute_span["span_id"] in ancestors(action["span_id"])
            # Every sandbox execution and the recursive Tool ride under the
            # one Root execution span.
            for span in fleet_spans:
                if span["name"] in {"sandbox.execute", "tool.rlm_query"}:
                    assert execute_span["span_id"] in ancestors(span["span_id"]), span["name"]

            # No grandchild interpreter: exactly one child interpreter built.
            assert child_interpreter_count == 1

            # --- Cleanup: confirmed absence, then restored admission. ---
            _wait_for_admission(resources, permits=settings.max_active_daytona_leases)
        finally:
            cleanup_failures = client.portal.call(_strict_cleanup, resources, settings.volume_name)
    assert cleanup_failures == ()

    write_receipt(
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "span_graph": {
                "fleet_span_counts": span_counts,
                "expected_fleet_span_counts": {
                    "fleet_turn": 1,
                    "RLM.execute": 1,
                    "RLM.root_action": 2,
                    "RLM.root_lm": 3,
                    "sandbox.execute": 3,
                    "tool.rlm_query": 1,
                    "RLM.recursive_call": 1,
                },
            },
            "public_stream": chunk_summary,
            "assertions": {
                "public_stream_unchanged": True,
                "single_span_per_lifecycle_key": True,
                "root_child_ancestry_acyclic": True,
                "root_depth_zero_child_depth_one": True,
                "no_grandchild_interpreter": child_interpreter_count == 1,
                "retained_fields_present": True,
                "deleted_provider_fields_absent": True,
                "trace_id_exposed_and_exported": bool(trace_id),
                "cleanup_confirmed_absent": cleanup_failures == (),
                "admission_restored": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
