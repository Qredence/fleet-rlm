#!/usr/bin/env python3
"""Evaluate PDF needle-in-haystack retrieval for the Enterprise 2030 report.

Runs gold Q&A items through routing preview (H2) and optional live RLM (H1/H2 full).

Usage:
    # Fast routing-only baseline (no LLM / Daytona):
    uv run python scripts/evaluate_pdf_needle_retrieval.py --routing-only

    # Full eval (requires DSPY + DAYTONA env):
    uv run python scripts/evaluate_pdf_needle_retrieval.py \
        --dataset .data/datasets/enterprise-2030-needle-eval.json \
        --pdf output/the-enterprise-in-2030-report-copy.pdf \
        --output-dir output/pdf-needle-eval \
        --limit 3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger("pdf_needle_eval")

DEFAULT_DATASET = ROOT / ".data/datasets/enterprise-2030-needle-eval.json"
DEFAULT_PDF = ROOT / "output" / "the-enterprise-in-2030-report-copy.pdf"
CANONICAL_PDF = "/Volumes/SSD-T7/qredence-environnement/fleet-rlm/output/the-enterprise-in-2030-report-copy.pdf"


def load_dataset(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}")
    return data


def _bootstrap_history() -> Any:
    import dspy

    return dspy.History(messages=[])


def _build_turn_context(
    *,
    query: str,
    pdf_path: str,
    include_path: bool,
    history: Any | None = None,
    loaded_document_paths: list[str] | None = None,
    session_context_paths: list[str] | None = None,
) -> Any:
    from fleet_rlm.runtime.modules.context_routing import build_turn_context

    message_paths = [pdf_path] if include_path else None
    return build_turn_context(
        user_request=query,
        history=history,
        context_paths=message_paths,
        loaded_document_paths=loaded_document_paths,
        session_context_paths=session_context_paths,
    )


def _preview_route(module: Any, *, query: str, turn_context: Any) -> dict[str, Any]:
    preview = module.preview_routing(
        user_request=query,
        execution_mode="auto",
        turn_context=turn_context,
    )
    return preview if isinstance(preview, dict) else {}


def _route_name(preview: dict[str, Any]) -> str:
    return str(preview.get("routing_decision") or "auto")


def _collect_trajectory(interpreter: Any) -> list[str]:
    steps: list[str] = []
    callback_steps = getattr(interpreter, "_turn_step_log", None)
    if isinstance(callback_steps, list):
        for step in callback_steps:
            if isinstance(step, dict):
                for key in ("code", "code_preview", "reasoning", "output", "observation"):
                    value = step.get(key)
                    if value:
                        steps.append(str(value))
            else:
                steps.append(str(step))
    return steps


def _install_trajectory_logger(interpreter: Any) -> None:
    log: list[dict[str, Any]] = []

    def _callback(payload: dict[str, Any]) -> None:
        log.append(dict(payload))

    interpreter._turn_step_log = log  # noqa: SLF001
    interpreter._turn_step_callback = _callback  # noqa: SLF001


def run_h2_routing_only(
    module: Any,
    item: dict[str, Any],
    *,
    pdf_path: str,
    session_context_paths: list[str] | None = None,
    loaded_document_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """H2 routing preview for with-path and pathless conditions."""
    results: list[dict[str, Any]] = []
    history = _bootstrap_history()

    for condition, include_path in (
        ("path_every_turn", True),
        ("pathless_followup", False),
    ):
        turn_context = _build_turn_context(
            query=item["query"],
            pdf_path=pdf_path,
            include_path=include_path,
            history=history,
            session_context_paths=session_context_paths,
            loaded_document_paths=loaded_document_paths,
        )
        preview = _preview_route(module, query=item["query"], turn_context=turn_context)
        results.append(
            {
                "backend": "h2_routing",
                "condition": condition,
                "route": _route_name(preview),
                "preview": preview,
                "estimated_chars": getattr(turn_context, "estimated_chars", 0),
                "context_paths": list(getattr(turn_context, "context_paths", []) or []),
            }
        )
    return results


def run_h1_direct_rlm(
    interpreter: Any,
    item: dict[str, Any],
    *,
    pdf_path: str,
    document_text: str,
) -> dict[str, Any]:
    """H1 upper bound: RLM with document_text preloaded."""
    from fleet_rlm.runtime.modules.variable_mode import build_variable_mode_rlm

    _install_trajectory_logger(interpreter)
    rlm = build_variable_mode_rlm(
        interpreter=interpreter,
        max_iterations=12,
        max_llm_calls=24,
        verbose=False,
        sub_lm=getattr(interpreter, "sub_lm", None),
    )
    prompt = (
        "Large PDF needle retrieval task. Search document_text with Python (find/regex) "
        "before answering. Do not open host filesystem paths.\n\n"
        f"Task: {item['query']}"
    )
    started = time.time()
    try:
        prediction = rlm(
            task=item["query"],
            prompt=prompt,
            history=_bootstrap_history(),
            document_text=document_text,
            context_paths=[pdf_path],
        )
        answer = str(getattr(prediction, "answer", "") or "")
        status = "ok"
        error = ""
    except Exception as exc:
        answer = ""
        status = "error"
        error = str(exc)
    elapsed_ms = int((time.time() - started) * 1000)
    return {
        "backend": "h1_direct_rlm",
        "condition": "document_text_preloaded",
        "status": status,
        "error": error,
        "answer": answer,
        "elapsed_ms": elapsed_ms,
        "trajectory": _collect_trajectory(interpreter),
        "route": "large_context_rlm",
    }


def run_h2_full_agent(
    module: Any,
    interpreter: Any,
    item: dict[str, Any],
    *,
    pdf_path: str,
    include_path: bool,
    session_context_paths: list[str] | None = None,
    loaded_document_paths: list[str] | None = None,
) -> dict[str, Any]:
    """H2 full agent path via EscalatingFleetModule."""
    _install_trajectory_logger(interpreter)
    turn_context = _build_turn_context(
        query=item["query"],
        pdf_path=pdf_path,
        include_path=include_path,
        history=_bootstrap_history(),
        session_context_paths=session_context_paths,
        loaded_document_paths=loaded_document_paths,
    )
    preview = _preview_route(module, query=item["query"], turn_context=turn_context)
    started = time.time()
    try:
        prediction = module(
            user_request=item["query"],
            core_memory="",
            history=_bootstrap_history(),
            execution_mode="auto",
            turn_context=turn_context,
        )
        answer = str(
            getattr(prediction, "answer", None)
            or getattr(prediction, "assistant_response", None)
            or getattr(prediction, "response", None)
            or ""
        )
        status = "ok"
        error = ""
    except Exception as exc:
        answer = ""
        status = "error"
        error = str(exc)
    elapsed_ms = int((time.time() - started) * 1000)
    route = (
        str(getattr(prediction, "routing_decision", None) or _route_name(preview))
        if status == "ok"
        else _route_name(preview)
    )
    return {
        "backend": "h2_full_agent",
        "condition": "path_every_turn" if include_path else "pathless_followup",
        "status": status,
        "error": error,
        "answer": answer,
        "elapsed_ms": elapsed_ms,
        "trajectory": _collect_trajectory(interpreter),
        "route": route,
        "preview": preview,
    }


def _overall_verdict(scores: dict[str, Any], item: dict[str, Any]) -> str:
    if item.get("negative_control"):
        return "PASS" if scores.get("abstention_verdict") == "ABSTAIN_PASS" else "FAIL"
    quote_verdict = scores.get("quote_verdict")
    if quote_verdict == "PASS" and scores.get("routing", 0) >= 1.0:
        return "PASS"
    if quote_verdict == "PARTIAL":
        return "PARTIAL"
    return "FAIL"


def evaluate_item(
    item: dict[str, Any],
    *,
    module: Any | None,
    interpreter: Any | None,
    pdf_path: str,
    document_text: str,
    routing_only: bool,
    run_h1: bool,
    run_h2_full: bool,
    session_context_paths: list[str] | None,
    loaded_document_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    from fleet_rlm.quality.pdf_needle_scoring import score_needle_result

    rows: list[dict[str, Any]] = []

    if module is not None:
        for routing_row in run_h2_routing_only(
            module,
            item,
            pdf_path=pdf_path,
            session_context_paths=session_context_paths,
            loaded_document_paths=loaded_document_paths,
        ):
            scores = score_needle_result(
                item,
                answer="",
                route=routing_row["route"],
                trajectory="",
            )
            row = {
                "item_id": item["id"],
                "category": item.get("category"),
                "backend": routing_row["backend"],
                "condition": routing_row["condition"],
                "route": routing_row["route"],
                "estimated_chars": routing_row["estimated_chars"],
                "context_paths": routing_row["context_paths"],
                "scores": scores.to_dict(),
                "answer_excerpt": "",
                "trajectory_notes": "routing-only",
                "verdict": "PASS" if scores.routing >= 1.0 or item.get("negative_control") else "FAIL",
            }
            rows.append(row)

    if routing_only:
        return rows

    if run_h1 and interpreter is not None:
        h1 = run_h1_direct_rlm(interpreter, item, pdf_path=pdf_path, document_text=document_text)
        scores = score_needle_result(
            item,
            answer=h1["answer"],
            route=h1["route"],
            trajectory=h1.get("trajectory"),
        )
        rows.append(
            {
                "item_id": item["id"],
                "category": item.get("category"),
                **h1,
                "scores": scores.to_dict(),
                "answer_excerpt": h1["answer"][:400],
                "trajectory_notes": "; ".join(h1.get("trajectory", [])[:3])[:500],
                "verdict": _overall_verdict(scores.to_dict(), item),
            }
        )

    if run_h2_full and module is not None and interpreter is not None:
        for include_path in (True, False):
            h2 = run_h2_full_agent(
                module,
                interpreter,
                item,
                pdf_path=pdf_path,
                include_path=include_path,
                session_context_paths=session_context_paths,
                loaded_document_paths=loaded_document_paths,
            )
            scores = score_needle_result(
                item,
                answer=h2["answer"],
                route=h2["route"],
                trajectory=h2.get("trajectory"),
            )
            rows.append(
                {
                    "item_id": item["id"],
                    "category": item.get("category"),
                    **h2,
                    "scores": scores.to_dict(),
                    "answer_excerpt": h2["answer"][:400],
                    "trajectory_notes": "; ".join(h2.get("trajectory", [])[:3])[:500],
                    "verdict": _overall_verdict(scores.to_dict(), item),
                }
            )

    return rows


def build_escalating_module(interpreter: Any) -> Any:
    from fleet_rlm.runtime.modules import EscalatingFleetModule

    return EscalatingFleetModule(interpreter=interpreter, verbose=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate PDF needle retrieval")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "pdf-needle-eval")
    parser.add_argument("--routing-only", action="store_true", help="Only run H2 routing preview (no LLM)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--item-ids", nargs="*", default=None)
    parser.add_argument("--skip-h1", action="store_true")
    parser.add_argument("--skip-h2-full", action="store_true")
    parser.add_argument(
        "--session-context-paths",
        action="store_true",
        help="Simulate persisted session context_paths for pathless follow-ups",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.dataset.is_file():
        logger.error("Dataset not found: %s", args.dataset)
        return 1
    if not args.pdf.is_file():
        logger.error("PDF not found: %s", args.pdf)
        return 1

    items = load_dataset(args.dataset)
    if args.item_ids:
        wanted = set(args.item_ids)
        items = [item for item in items if item["id"] in wanted]
    if args.limit is not None:
        items = items[: args.limit]

    from fleet_rlm.quality.pdf_needle_scoring import aggregate_needle_results
    from fleet_rlm.runtime.content.ingestion import read_document_content

    document_text, _ = read_document_content(args.pdf)
    pdf_path = str(args.pdf.resolve())

    module: Any | None = None
    interpreter: Any | None = None
    if args.routing_only:
        module = build_escalating_module(interpreter=None)
    else:
        from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

        interpreter = DaytonaInterpreter()
        module = build_escalating_module(interpreter)

    session_paths = [pdf_path] if args.session_context_paths else None
    loaded_paths = [pdf_path] if args.session_context_paths else None
    all_rows: list[dict[str, Any]] = []
    for item in items:
        logger.info("Evaluating %s (%s)", item["id"], item.get("category"))
        rows = evaluate_item(
            item,
            module=module,
            interpreter=interpreter,
            pdf_path=pdf_path,
            document_text=document_text,
            routing_only=args.routing_only,
            run_h1=not args.skip_h1,
            run_h2_full=not args.skip_h2_full and not args.routing_only,
            session_context_paths=session_paths,
            loaded_document_paths=loaded_paths,
        )
        all_rows.extend(rows)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    results_path = run_dir / "results.json"
    summary_path = run_dir / "summary.json"
    results_path.write_text(json.dumps(all_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = {
        "timestamp": timestamp,
        "dataset": str(args.dataset),
        "pdf": pdf_path,
        "routing_only": args.routing_only,
        "session_context_paths_simulated": bool(session_paths),
        "items_evaluated": len(items),
        "rows": len(all_rows),
        "aggregates": aggregate_needle_results(all_rows),
        "failure_taxonomy": _failure_taxonomy(all_rows),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    latest_summary = args.output_dir / "latest-summary.json"
    latest_summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    logger.info("Wrote %s", results_path)
    logger.info("Wrote %s", summary_path)
    agg = summary["aggregates"]
    logger.info(
        "routing_accuracy=%.2f trajectory_grounding_rate=%.2f followup_regression_rate=%.2f",
        agg.get("routing_accuracy", 0),
        agg.get("trajectory_grounding_rate", 0),
        agg.get("followup_regression_rate", 0),
    )
    return 0


def _failure_taxonomy(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "routing_failure": 0,
        "quote_failure": 0,
        "trajectory_failure": 0,
        "abstention_failure": 0,
    }
    for row in rows:
        scores = row.get("scores", {})
        if row.get("condition") == "pathless_followup" and scores.get("routing", 0) < 1.0:
            counts["routing_failure"] += 1
        if scores.get("quote_verdict") == "FAIL":
            counts["quote_failure"] += 1
        if scores.get("trajectory_verdict") == "FAIL":
            counts["trajectory_failure"] += 1
        if scores.get("abstention_verdict") == "ABSTAIN_FAIL":
            counts["abstention_failure"] += 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
