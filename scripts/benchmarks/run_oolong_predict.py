"""Run one or more Oolong predict rows through the Fleet native RLM adapter.

Default mode is credential-free dry execution: load one HF-shaped row (fixture by
default), build locked Fleet kwargs, score a canned answer with the official
Oolong helpers, and write a bounded receipt. ``--live`` requires ``FLEET_LIVE=1``,
stages ``context_window_text`` through ``AttachmentContextCapsule``, and invokes
``build_native_rlm`` with a caller-owned Daytona interpreter.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fleet_rlm.config.loader import require_live_execution
from scripts.benchmarks.campaign import write_receipt_once
from scripts.benchmarks.oolong.adapter import (
    DEFAULT_FIXTURE,
    build_predict_kwargs,
    build_receipt,
    invoke_live_prediction,
    kwargs_context_mode,
    resolve_datapoints,
    score_prediction,
    stage_attachment_context_on_lease,
)

RECEIPT_SCHEMA = "fleet.oolong-predict/v1"
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_DEFAULT_MODEL = "oolong-predict-adapter"


class OolongPredictError(RuntimeError):
    """Bounded predict adapter failure."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="exclusive receipt path")
    parser.add_argument("--dataset", choices=("synth", "real"), default="synth")
    parser.add_argument("--split", default="validation", help="HF split (synth: validation|test)")
    parser.add_argument("--index", type=int, default=0, help="starting row index")
    parser.add_argument("--limit", type=int, default=1, help="rows to score (default N=1 dry)")
    parser.add_argument(
        "--hf",
        action="store_true",
        help="load rows from Hugging Face instead of the bundled offline fixture",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="offline HF-shaped row used unless --hf is set",
    )
    parser.add_argument(
        "--answer",
        default="",
        help="canned model answer for dry mode; defaults to a gold-aligned label string on fixtures",
    )
    parser.add_argument(
        "--model-name",
        default=_DEFAULT_MODEL,
        help="model label recorded in official Oolong score payloads",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="run provider-backed predict with AttachmentContextCapsule staging",
    )
    parser.add_argument(
        "--mlflow-url",
        default="",
        help="optional MLflow tracking URI; skipped when unset",
    )
    parser.add_argument(
        "--mlflow-experiment",
        default="",
        help="MLflow experiment name when --mlflow-url is set (defaults to oolong-predict)",
    )
    return parser


def _require_live_flag() -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise OolongPredictError("FLEET_LIVE=1 is required for live Oolong predict")


def _default_dry_answer(datapoint: Mapping[str, object]) -> str:
    answer = datapoint.get("answer")
    if isinstance(answer, str) and answer.startswith("[") and datapoint.get("answer_type") == "ANSWER_TYPE.LABEL":
        try:
            import ast

            label = ast.literal_eval(answer)[0]
            return f"Label: {label}"
        except (SyntaxError, ValueError, IndexError, TypeError):
            pass
    return "Label: spam"


def _fixture_path(args: argparse.Namespace) -> Path | None:
    return None if args.hf else args.fixture


def _run_dry(args: argparse.Namespace) -> dict[str, object]:
    fixture = _fixture_path(args)
    loaded = resolve_datapoints(
        dataset=args.dataset,
        split=args.split,
        start_index=args.index,
        limit=args.limit,
        fixture=fixture,
    )
    answer = args.answer.strip() or _default_dry_answer(loaded[0].row)
    rows: list[dict[str, object]] = []
    scores: list[dict[str, object]] = []
    for item in loaded:
        kwargs = build_predict_kwargs(item.row, mode="dry_shortcut")
        score = score_prediction(item.row, answer, dataset=args.dataset, model_name=args.model_name)
        rows.append(
            {
                "id": item.row.get("id"),
                "context_window_id": item.row.get("context_window_id"),
                "dataset": item.row.get("dataset"),
                "source": item.source,
                "index": item.index,
                "request_chars": len(str(kwargs.get("request", ""))),
                "context_mode": kwargs_context_mode(kwargs),
                "mocked": {
                    "daytona_interpreter": True,
                    "provider_llm": True,
                    "answer_source": "operator_or_default_canned",
                },
            }
        )
        scores.append(score)
    revision = next((item.dataset_revision for item in loaded if item.dataset_revision), None)
    return build_receipt(
        mode="dry",
        dataset=args.dataset,
        split=args.split,
        limit=args.limit,
        rows=rows,
        scores=scores,
        context_mode="dry_request_concat",
        model_name=args.model_name,
        dataset_revision=revision,
        source=loaded[0].source,
    )


async def _run_live_async(args: argparse.Namespace, settings: Any) -> dict[str, object]:
    from fleet_rlm.daytona.provisioning import acquire_ephemeral_interpreter

    loaded = resolve_datapoints(
        dataset=args.dataset,
        split=args.split,
        start_index=args.index,
        limit=args.limit,
        fixture=_fixture_path(args),
    )
    rows: list[dict[str, object]] = []
    scores: list[dict[str, object]] = []
    for item in loaded:
        lease = await acquire_ephemeral_interpreter(settings, purpose="oolong-predict")
        try:
            context_text = str(item.row.get("context_window_text", ""))
            capsule = await stage_attachment_context_on_lease(lease, context_text)
            kwargs = build_predict_kwargs(
                item.row,
                mode="production",
                session_id=lease.session_id,
                attachment_context=capsule,
            )
            answer = await invoke_live_prediction(settings, kwargs, interpreter=lease.interpreter)
        finally:
            with contextlib.suppress(Exception):
                lease.interpreter.shutdown()
            with contextlib.suppress(Exception):
                await lease.platform.delete(lease.sandbox)
        score = score_prediction(item.row, answer, dataset=args.dataset, model_name=args.model_name)
        rows.append(
            {
                "id": item.row.get("id"),
                "context_window_id": item.row.get("context_window_id"),
                "dataset": item.row.get("dataset"),
                "source": item.source,
                "index": item.index,
                "request_chars": len(str(kwargs.get("request", ""))),
                "context_mode": kwargs_context_mode(kwargs),
                "answer_chars": len(answer),
                "mocked": {"provider_llm": False, "daytona_interpreter": False},
            }
        )
        scores.append(score)
    revision = next((item.dataset_revision for item in loaded if item.dataset_revision), None)
    return build_receipt(
        mode="live",
        dataset=args.dataset,
        split=args.split,
        limit=args.limit,
        rows=rows,
        scores=scores,
        context_mode="attachment_context_capsule",
        model_name=args.model_name,
        dataset_revision=revision,
        source=loaded[0].source,
    )


def _maybe_log_mlflow(args: argparse.Namespace, receipt: Mapping[str, object]) -> None:
    if not args.mlflow_url:
        return
    _require_live_flag()
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_url)
    experiment = args.mlflow_experiment.strip() or "oolong-predict"
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name="oolong-predict"):
        mlflow.log_param("fleet.oolong.dataset", receipt.get("dataset"))
        mlflow.log_param("fleet.oolong.mode", receipt.get("mode"))
        mlflow.log_param("fleet.oolong.context_mode", receipt.get("context_mode"))
        summary = receipt.get("summary")
        if isinstance(summary, Mapping) and isinstance(summary.get("mean"), (int, float)):
            mlflow.log_metric("fleet.oolong.score_mean", float(summary["mean"]))


def _run_live(args: argparse.Namespace) -> dict[str, object]:
    _require_live_flag()
    load_dotenv(_REPO_ROOT / ".env", override=False)
    settings = require_live_execution()
    return asyncio.run(_run_live_async(args, settings))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output.expanduser().resolve()
    try:
        if args.limit < 1:
            raise OolongPredictError("limit must be at least 1")
        receipt = _run_live(args) if args.live else _run_dry(args)
        if receipt.get("schema") != RECEIPT_SCHEMA:
            raise OolongPredictError("receipt schema mismatch")
        write_receipt_once(output, receipt)
        _maybe_log_mlflow(args, receipt)
        return 0
    except Exception as exc:
        failure = build_receipt(
            mode="live" if args.live else "dry",
            dataset=args.dataset,
            split=args.split,
            limit=args.limit,
            rows=(),
            scores=(),
            context_mode="attachment_context_capsule" if args.live else "dry_request_concat",
            model_name=args.model_name,
            dataset_revision=None,
            source="huggingface" if args.hf else "fixture",
            status="failed",
            error_category=type(exc).__name__,
        )
        with contextlib.suppress(Exception):
            write_receipt_once(output, failure)
        print(f"oolong predict failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
