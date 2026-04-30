#!/usr/bin/env python3
"""Generate a markdown comparison report from pilot LongCoT benchmark results."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def find_latest_eval_file(output_dir: Path, mode: str) -> Path | None:
    """Find the latest eval JSON for a given mode.

    Direct-mode eval files do not contain 'rlm' in their name.
    RLM-mode eval files contain 'rlm' in their name.
    """
    files = sorted(
        output_dir.glob("longcot-eval-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if mode == "direct":
        candidates = [f for f in files if "rlm" not in f.name]
    elif mode == "rlm":
        candidates = [f for f in files if "rlm" in f.name]
    else:
        candidates = files
    return candidates[0] if candidates else None


def find_transport_summary(output_dir: Path) -> Path | None:
    """Find the RLM transport summary JSON."""
    path = output_dir / "longcot-rlm-transport-summary.json"
    return path if path.exists() else None


def generate_report(output_dir: Path) -> Path:
    """Generate a markdown comparison report from pilot benchmark results."""
    direct_eval_path = find_latest_eval_file(output_dir, "direct")
    rlm_eval_path = find_latest_eval_file(output_dir, "rlm")

    if not direct_eval_path:
        raise FileNotFoundError(f"No direct-mode eval JSON found in {output_dir}")
    if not rlm_eval_path:
        raise FileNotFoundError(f"No RLM-mode eval JSON found in {output_dir}")

    direct_eval = json.loads(direct_eval_path.read_text(encoding="utf-8"))
    rlm_eval = json.loads(rlm_eval_path.read_text(encoding="utf-8"))

    transport_summary = None
    transport_path = find_transport_summary(output_dir)
    if transport_path:
        transport_summary = json.loads(transport_path.read_text(encoding="utf-8"))

    # Extract key metrics
    direct_total = direct_eval.get("total", 0)
    direct_correct = direct_eval.get("correct", 0)
    direct_incorrect = direct_eval.get("incorrect", 0)
    direct_failed = direct_eval.get("failed", 0)
    direct_accuracy = direct_eval.get("accuracy", 0.0)
    direct_overall = direct_eval.get("overall_accuracy", 0.0)

    rlm_correct = rlm_eval.get("correct", 0)
    rlm_incorrect = rlm_eval.get("incorrect", 0)
    rlm_failed = rlm_eval.get("failed", 0)
    rlm_accuracy = rlm_eval.get("accuracy", 0.0)
    rlm_overall = rlm_eval.get("overall_accuracy", 0.0)

    delta_accuracy = rlm_accuracy - direct_accuracy
    delta_overall = rlm_overall - direct_overall

    # Build per-domain breakdown
    direct_by_domain: dict[str, dict[str, int]] = {}
    for d in direct_eval.get("details", []):
        dom = d.get("domain", "unknown")
        if dom not in direct_by_domain:
            direct_by_domain[dom] = {
                "correct": 0,
                "incorrect": 0,
                "failed": 0,
                "total": 0,
            }
        direct_by_domain[dom][d.get("status", "failed")] = (
            direct_by_domain[dom].get(d.get("status", "failed"), 0) + 1
        )
        direct_by_domain[dom]["total"] += 1

    rlm_by_domain: dict[str, dict[str, int]] = {}
    for d in rlm_eval.get("details", []):
        dom = d.get("domain", "unknown")
        if dom not in rlm_by_domain:
            rlm_by_domain[dom] = {"correct": 0, "incorrect": 0, "failed": 0, "total": 0}
        rlm_by_domain[dom][d.get("status", "failed")] = (
            rlm_by_domain[dom].get(d.get("status", "failed"), 0) + 1
        )
        rlm_by_domain[dom]["total"] += 1

    # Transport rates
    transport_success_rate = 0.0
    transport_tasks_total = 0
    transport_tasks_successful = 0
    if transport_summary:
        transport_success_rate = transport_summary.get("success_rate", 0.0)
        transport_tasks_total = transport_summary.get("tasks_total", 0)
        transport_tasks_successful = transport_summary.get("tasks_successful", 0)

    # Model info
    model = "DeepSeek V4 Flash"
    provider = "OpenRouter"

    timestamp = datetime.now(UTC).isoformat()

    lines = [
        "# LongCoT Pilot Benchmark Comparison Report",
        "",
        f"**Generated:** {timestamp}",
        "",
        "## Model Information",
        "",
        f"- **Model:** {model}",
        f"- **Provider:** {provider}",
        "- **Benchmark:** LongCoT (longcot-mini)",
        f"- **Tasks:** {direct_total}",
        "",
        "## Overall Results",
        "",
        "| Metric | Direct Mode | RLM Mode | Delta (RLM − Direct) |",
        "|--------|-------------|----------|----------------------|",
        f"| Accuracy | {direct_accuracy:.2%} | {rlm_accuracy:.2%} | **{delta_accuracy:+.2%}** |",
        f"| Overall Accuracy | {direct_overall:.2%} | {rlm_overall:.2%} | **{delta_overall:+.2%}** |",
        f"| Correct | {direct_correct} | {rlm_correct} | {rlm_correct - direct_correct:+d} |",
        f"| Incorrect | {direct_incorrect} | {rlm_incorrect} | {rlm_incorrect - direct_incorrect:+d} |",
        f"| Failed | {direct_failed} | {rlm_failed} | {rlm_failed - direct_failed:+d} |",
        "",
        "## Per-Domain Breakdown",
        "",
    ]

    all_domains = sorted(set(direct_by_domain.keys()) | set(rlm_by_domain.keys()))
    if all_domains:
        lines.append(
            "| Domain | Direct Correct | Direct Incorrect | Direct Failed | RLM Correct | RLM Incorrect | RLM Failed |"
        )
        lines.append(
            "|--------|---------------:|-----------------:|--------------:|------------:|--------------:|-----------:|"
        )
        for dom in all_domains:
            dc = direct_by_domain.get(dom, {})
            rc = rlm_by_domain.get(dom, {})
            lines.append(
                f"| {dom} | "
                f"{dc.get('correct', 0)} | "
                f"{dc.get('incorrect', 0)} | "
                f"{dc.get('failed', 0)} | "
                f"{rc.get('correct', 0)} | "
                f"{rc.get('incorrect', 0)} | "
                f"{rc.get('failed', 0)} |"
            )
    else:
        lines.append("_No per-domain data available._")

    lines.extend(
        [
            "",
            "## RLM Transport Success",
            "",
        ]
    )

    if transport_summary:
        lines.extend(
            [
                f"- **Transport success rate:** {transport_success_rate:.2%}",
                f"- **Successful responses:** {transport_tasks_successful} / {transport_tasks_total}",
            ]
        )
    else:
        lines.append("_Transport summary not available._")

    lines.extend(
        [
            "",
            "## Execution Timestamps",
            "",
            f"- **Direct eval file:** `{direct_eval_path.name}`",
            f"- **RLM eval file:** `{rlm_eval_path.name}`",
        ]
    )

    if transport_summary:
        lines.append(f"- **RLM transport summary:** `{transport_path.name}`")

    lines.extend(
        [
            "",
            "## Source Files",
            "",
            f"- Direct eval: `{direct_eval_path}`",
            f"- RLM eval: `{rlm_eval_path}`",
        ]
    )

    report_path = (
        output_dir
        / f"comparison-report-{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md"
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Comparison report written to: {report_path}")
    return report_path


if __name__ == "__main__":
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/longcot-eval")
    output_dir = output_dir.resolve()
    report = generate_report(output_dir)
    print(report)
