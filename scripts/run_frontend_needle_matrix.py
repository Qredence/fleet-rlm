#!/usr/bin/env python3
"""Simulate the workspace frontend needle matrix and write frontend-runs.jsonl.

Mirrors frontend path detection (detectContextPaths) plus post-bootstrap session
persistence (loaded_document_paths / interpreter.context_paths).

Usage:
    uv run python scripts/run_frontend_needle_matrix.py
    uv run python scripts/run_frontend_needle_matrix.py --item-ids chad_gates_quote akiyuki_ui_quote
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_DATASET = ROOT / ".data/datasets/enterprise-2030-needle-eval.json"
DEFAULT_PDF = ROOT / "output/the-enterprise-in-2030-report-copy.pdf"
OUTPUT_JSONL = ROOT / "output/pdf-needle-eval/frontend-runs.jsonl"

PATH_CANDIDATE_RE = re.compile(r"(?:^|[\s(\"'`])((?:~\/|\/|\.\.\/|\.\/)[^\s\"'`<>()\[\]{}]+)")


def detect_context_paths(value: str) -> list[str]:
    """Frontend-compatible path detection from chat message text."""
    detected: list[str] = []
    seen: set[str] = set()
    for match in PATH_CANDIDATE_RE.finditer(value):
        candidate = match.group(1).rstrip(".,!?;:\"')]}")
        if not candidate or candidate == "/" or "://" in candidate:
            continue
        if candidate not in seen:
            seen.add(candidate)
            detected.append(candidate)
    return detected


def build_frontend_message(query: str, *, pdf_path: str, include_path: bool) -> str:
    if include_path:
        return f"{query}\n\nPDF: {pdf_path}"
    return query


def run_matrix(
    items: list[dict[str, Any]],
    *,
    pdf_path: str,
    simulate_persisted_session: bool,
) -> list[dict[str, Any]]:
    from fleet_rlm.quality.pdf_needle_scoring import score_needle_result
    from fleet_rlm.runtime.modules import EscalatingFleetModule
    from fleet_rlm.runtime.modules.context_routing import build_turn_context

    module = EscalatingFleetModule(interpreter=None)
    session_paths = [pdf_path] if simulate_persisted_session else None
    loaded_paths = [pdf_path] if simulate_persisted_session else []

    rows: list[dict[str, Any]] = []
    for item in items:
        for condition, include_path in (
            ("path_every_turn", True),
            ("pathless_followup", False),
        ):
            message = build_frontend_message(item["query"], pdf_path=pdf_path, include_path=include_path)
            inferred_paths = detect_context_paths(message)
            turn_context = build_turn_context(
                user_request=message,
                context_paths=inferred_paths or None,
                loaded_document_paths=loaded_paths,
                session_context_paths=session_paths,
            )
            preview = module.preview_routing(
                user_request=message,
                execution_mode="auto",
                turn_context=turn_context,
            )
            route = str(preview.get("routing_decision") or "auto")
            scores = score_needle_result(item, answer="", route=route, trajectory="")
            row = {
                "timestamp": datetime.now(UTC).isoformat(),
                "item_id": item["id"],
                "category": item.get("category"),
                "condition": condition,
                "message_preview": message[:200],
                "inferred_context_paths": inferred_paths,
                "persisted_session_paths": session_paths or [],
                "route": route,
                "estimated_chars": getattr(turn_context, "estimated_chars", 0),
                "scores": scores.to_dict(),
                "answer_excerpt": "",
                "trajectory_notes": "routing-preview-only",
                "verdict": "PASS" if scores.routing >= 1.0 or item.get("negative_control") else "FAIL",
            }
            rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frontend needle matrix simulation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output", type=Path, default=OUTPUT_JSONL)
    parser.add_argument("--item-ids", nargs="*", default=None)
    parser.add_argument(
        "--no-persisted-session",
        action="store_true",
        help="Disable simulated post-bootstrap session context (pre-fix behavior)",
    )
    args = parser.parse_args()

    items = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.item_ids:
        wanted = set(args.item_ids)
        items = [item for item in items if item["id"] in wanted]

    pdf_path = str(args.pdf.resolve())
    rows = run_matrix(
        items,
        pdf_path=pdf_path,
        simulate_persisted_session=not args.no_persisted_session,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Appended {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
